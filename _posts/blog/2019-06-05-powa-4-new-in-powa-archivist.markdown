---
layout: post
title: "PoWA 4: changes in powa-archivist!"
modified:
categories: postgresql
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: gb
image:
  feature:
date: 2019-06-05T16:26:17+02:00
---

This article is part of the [PoWA 4 beta](http://powa.readthedocs.io/) series,
and describes the changes done in
[powa-archivist](https://powa.readthedocs.io/en/latest/components/powa-archivist/index.html).

For more information about this v4, you can consult the [general introduction
article]({% post_url
blog/2019-05-17-powa-4-with-remote-mode-beta-is-available %}).


### Quick overview

First of all, you have to know that there is not upgrade possible from v3 to
v4, so a `DROP EXTENSION powa` is required if you were already using PoWA on
any of your servers.  This is because this v4 involved **a lot** of changes in
the SQL part of the extension, making it the most significant change in the
PoWA suite for this new version.  Looking at the amount changes at the time I'm
writing this article, I get:

{% highlight diff %}
 CHANGELOG.md       |   14 +
 powa--4.0.0dev.sql | 2075 +++++++++++++++++++++-------
 powa.c             |   44 +-
 3 files changed, 1629 insertions(+), 504 deletions(-)
{% endhighlight %}

The lack of upgrade shouldn't be a problem in practice though.  PoWA is a
performance tool, so it's intended to have data with high precision but with a
very limited history.  If you're looking for a general monitoring solution
keeping months of counters, PoWA is definitely not the tool you need.

### Configuring the list of *remote servers*

Concerning the features themselves, the first small change is that
powa-archivist does not require the [background
worker](https://www.postgresql.org/docs/current/bgworker.html) to be active
anymore, as it won't be used for remote setup.  That means that a PostgreSQL
restart is not needed needed anymore to install PoWA.  Obviously, a restart is still
required if you want to use the local setup, using the background worker, or if
you want to install additional extensions that themselves require a restart.

Then, as PoWA needs some configuration (frequency of snapshot, data retention
and so on), some new tables are added to be able to configure all of that.  The
new `powa_servers` table stores the configuration for all the remote instances
whose data should be stored on this instance.  This *local PoWA instance* is
call a **repository server** (that typically should be dedicated to storing
PoWA data), in opposition to **remote instances** which are the instances you
want to monitor.  The content of this table is pretty straightforward:

{% highlight sql %}
\d powa_servers
                              Table "public.powa_servers"
  Column   |   Type   | Collation | Nullable |                 Default
-----------+----------+-----------+----------+------------------------------------------
 id            | integer  |           | not null | nextval('powa_servers_id_seq'::regclass)
 hostname      | text     |           | not null |
 alias         | text     |           |          |
 port          | integer  |           | not null |
 username      | text     |           | not null |
 password      | text     |           |          |
 dbname        | text     |           | not null |
 frequency     | integer  |           | not null | 300
 powa_coalesce | integer  |           | not null | 100
 retention     | interval |           | not null | '1 day'::interval
{% endhighlight %}

If you already used PoWA, you should recognize most of the configuration
options, that are now stored here.  The new options are used to describe how to
connect to the *remote servers*, and can provide an alias to be displayed in
the UI.

You also probably noticed a **password** column here.   Storing a password in
plain text in this table is an heresy as far as security is concerned.  So, as
mentioned in the [PoWA security section of the
documentation](https://powa.readthedocs.io/en/latest/security.html#connection-on-remote-servers),
you can store a NULL password and use instead [any of the authentication method
that libpq supports](https://www.postgresql.org/docs/current/auth-methods.html)
(.pgpass file, certificate...).  That's strongly recommended for any non toy
setup.

Another table, the `powa_snapshot_metas` table, is also added to store some
metadata regarding each *remote server* snapshot information:

{% highlight sql %}
                                   Table "public.powa_snapshot_metas"
    Column    |           Type           | Collation | Nullable |                Default
--------------+--------------------------+-----------+----------+---------------------------------------
 srvid        | integer                  |           | not null |
 coalesce_seq | bigint                   |           | not null | 1
 snapts       | timestamp with time zone |           | not null | '-infinity'::timestamp with time zone
 aggts        | timestamp with time zone |           | not null | '-infinity'::timestamp with time zone
 purgets      | timestamp with time zone |           | not null | '-infinity'::timestamp with time zone
 errors       | text[]
{% endhighlight %}

That's basically a counter to track the number of snapshots done, the timestamp
for each kind of event that happened (snapshot, aggregate and purge), and a
text array to store any error happening during the snapshot, that the UI can
display.

### SQL API to configure the *remote servers*

While thoses table are simple, a [basic SQL API is available to register new
servers and configure
them](https://powa.readthedocs.io/en/latest/remote_setup.html#configure-powa-and-stats-extensions-on-each-remote-server).
Basically, 6 functions are available:

  * `powa_register_server()`, to declare a new *remote server*, and the list of
    extensions available on it
  * `powa_configure_server()` to update any setting for the specified *remote
    server* (using a JSON where the key is the name of the parameter to change,
    and the value is the new value to use)
  * `powa_deactivate_server()` to disable snapshots on the specified *remote
    server* (which actually is setting up the `frequency` to **-1**)
  * `powa_delete_and_purge_server()` to remove the specified *remote server*
    from the list of servers and remove all associated snapshot data
  * `powa_activate_extension()`, to declare that a new extension is available
    on the specified *remote server*
  * `powa_deactivate_extension()`, to specify that an extension is not available
    anymore on the specified *remote server*

Any action more complicated than this should be performed using plain SQL
queries.  Hopefully, there shouldn't be many other needs, and the tables are
straightforward so this shouldn't be a problem.  [Feel free to ask for more
functions](https://github.com/powa-team/powa-archivist/issues) if you feel the
need though.  Please also note that the UI doesn't allow you to call those
functions, as the UI is for now entirely **read only**.

### Performing *remote snapshots*

As metrics are now stored on a different PostgreSQL instance, we had to
extensively change the way *snapshots* (retrieving the data from a [stat
extension](https://powa.readthedocs.io/en/latest/components/stats_extensions/index.html)
and storing them in PoWA catalog [in a space efficient way]({% post_url
blog/2016-09-16-minimizing-tuple-overhead %})) are performed.

The list of all stat extensions, or *data sources*, that are available on a
**server** (either *remote* or *local*) and for which we should perform a
*snapshot* are configured in a table called `powa_functions`:

{% highlight sql %}
               Table "public.powa_functions"
     Column     |  Type   | Collation | Nullable | Default
----------------+---------+-----------+----------+---------
 srvid          | integer |           | not null |
 module         | text    |           | not null |
 operation      | text    |           | not null |
 function_name  | text    |           | not null |
 query_source   | text    |           |          |
 added_manually | boolean |           | not null | true
 enabled        | boolean |           | not null | true
 priority       | numeric |           | not null | 10
{% endhighlight %}

A new `query_source` field is added, that provides the name of a *source*
function, required to  support remote snapshot of any [stat
extensions](https://powa.readthedocs.io/en/latest/components/stats_extensions/index.html).
This function is used to export the counters provided by this extension on a
different server, in a dedicated *transient table*.  The *snapshot* function
will then perform the *snapshot* using those exported data instead of the one
provided by stat extensions locally when the remote mode is used.  Note that
the counters export and the remote snapshot is done automatically with the the
new [powa-collector
daemon](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html),
that I'll cover in another article.

Here's an example of how PoWA perform a *remote snapshot* of the list of
databases.  As you'll see, this is very simplistic, meaning that it's very easy
to add support for a new stat extension.

The *transient table*:

{% highlight sql %}
   Unlogged table "public.powa_databases_src_tmp"
 Column  |  Type   | Collation | Nullable | Default
---------+---------+-----------+----------+---------
 srvid   | integer |           | not null |
 oid     | oid     |           | not null |
 datname | name    |           | not null |
{% endhighlight %}

For better performance, all the *transient tables* are **unlogged**, as their
content is only needed during a *snapshot* and are trashed afterwards.  In this
example the *transient table* only stores the server identifier for which the
data are, the oid and name of each databases present on the *remote server*.

And the *source function*:

{% highlight sql %}
CREATE OR REPLACE FUNCTION public.powa_databases_src(_srvid integer,
    OUT oid oid, OUT datname name)
 RETURNS SETOF record
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF (_srvid = 0) THEN
        RETURN QUERY SELECT d.oid, d.datname
        FROM pg_database d;
    ELSE
        RETURN QUERY SELECT d.oid, d.datname
        FROM powa_databases_src_tmp d
        WHERE srvid = _srvid;
    END IF;
END;
$function$
{% endhighlight %}

This function simply returns the content of `pg_database` if local data are
asked (server id **0** is always the local server), or the content of the
*transient table* for the given remote server otherwise.

The *snapshot function* can then easily do any required work with the data
for the wanted *remote server*.  In the case of the `powa_databases_snapshot()`
function, the just synchronizing the list of databases, and storing the
timestamp of removal if a previously existing database is not found anymore.

For more details, you can consult the [PoWA datasource
integration](https://powa.readthedocs.io/en/latest/components/powa-archivist/development.html)
documentation, which was updated for the version 4 specificities.
