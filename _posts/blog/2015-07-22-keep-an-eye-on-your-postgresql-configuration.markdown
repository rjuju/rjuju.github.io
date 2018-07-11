---
redirect_from: "/postgresql/2015/07/20/keep-an-eye-on-your-postgresql-configuration.html"
layout: post
title: "Keep an eye on your PostgreSQL configuration"
modified:
categories: postgresql
excerpt:
tags: [monitoring, tuning, postgresql]
lang: gb
image:
  feature:
date: 2015-07-22T11:48:16+01:00
---

Have you ever wished to know what configuration changed during the last weeks,
when everything was so much faster, or wanted to check what happened on your
beloved cluster while you were in vacation?

[pg\_track\_settings](https://github.com/rjuju/pg_track_settings) is a simple,
SQL only extension that helps you to know all of that and more very easily.  As
it's designed as an extension, it requires PostgreSQL 9.1 or more.

### Some insights

As amost any extension, you have to compile it from source, or use the [pgxn
client](http://pgxnclient.projects.pgfoundry.org/), since there's no package
yet.  Assuming you just extract the tarball of the release 1.0.0 with a typical
server configuration:

{% highlight bash %}
$ cd pg_track_settings-1.0.0
$ sudo make install
{% endhighlight %}

Then the extension is available.  Create the extension on the database of your choice:

{% highlight sql %}
postgres=# CREATE EXTENSION pg_track_settings ;
CREATE EXTENSION
{% endhighlight %}

In order to historize the settings, you need to schedule a simple function call
on a regular basis.  This function is the **pg\_track\_settings\_snapshot**
function.  It's really cheap to call, and won't have any measurable impact on
your cluster.  This function will do all the smart work of storing all the
parameters **that changed since the last call**.

For instance, if you want to be able to know what changed on your server within
a 5 minutes accuracy, a simple cron entry like this for the postgres user is
enough:

{% highlight bash %}
*/5 *  * * *     psql -c "SELECT pg_track_settings_snapshot()" > /dev/null 2>&1
{% endhighlight %}

A background worker could be used on PostgreSQL 9.3 and more, but as we only
have to call one function every few minutes, it'd be overkill to add one just
for this.  If you really want one, you'd better consider settting up
[PoWA](http://dalibo.github.io/powa/) for that, or another extension that
allows to run task like [pgAgent](http://www.pgadmin.org/docs/dev/pgagent.html).

### How to use it

Let's call the snapshot function to get ti initial values:

{% highlight sql %}
postgres=# select pg_track_settings_snapshot()
 ----------------------------
  t
  (1 row)
{% endhighlight %}

A first snapshot with the initial settings values is saved.  Now, I'll just
change a setting in the **postgresql.conf** file (**ALTER SYSTEM** could also
be used on a PostgreSQL 9.4 or more release), reload the configuration and take
another snapshot:

{% highlight sql %}
postgres=# select pg_reload_conf();
 pg_reload_conf
 ----------------
  t
  (1 row)

postgres=# select * from pg_track_settings_snapshot();
 pg_track_settings_snapshot
----------------------------
 t
(1 row)
{% endhighlight %}

Now, the fun part.  What information is available?

First, what changed between two timestamp. For instance, let's check what
changed in the last 2 minutes:

{% highlight sql %}
postgres=# SELECT * FROM pg_track_settings_diff(now() - interval '2 minutes', now());
        name         | from_setting | from_exists | to_setting | to_exists
---------------------+--------------|-------------|------------|----------
 max_wal_size        | 93           | t           | 31         | t
(1 row)
{% endhighlight %}

What do we learn ?

  - as the max\_wal\_size parameter exists, I'm using the 9.5 alpha release.
    Yes, what PostgreSQL really needs right now is people testing the upcoming
    release!  It's simple, and the more people test it, the faster it'll be
    avalable.  See the [how to](https://wiki.postgresql.org/wiki/HowToBetaTest)
    page to see how you can help :)
  - the max\_wal\_size parameter existed 2 minutes ago (**from\_exists** is
    true), and also exists right now (**to\_exists** is true).  Obviously, the
    regular settings will not disappear, but think of extension related
    settings like pg\_stat\_statements.* or auto\_explain.*
  - the max\_wal\_size changed from **93** (**from\_setting**) to **31**
    (**to\_setting**).

Also, we can get the history of a specific setting:

{% highlight sql %}
postgres=# SELECT * FROM pg_track_settings_log('max_wal_size');
              ts               |     name     | setting_exists | setting
-------------------------------+--------------+----------------+---------
 2015-07-17 22:42:01.156948+02 | max_wal_size | t              | 31
 2015-07-17 22:38:02.722206+02 | max_wal_size | t              | 93
(2 rows)
{% endhighlight %}

You can also retrieve the entire configuration at a specified timestamp.  For
instance:

{% highlight sql %}
postgres=# SELECT * FROM pg_track_settings('2015-07-17 22:40:00');
                name                 |     setting
-------------------------------------+-----------------
[...]
 max_wal_senders                     | 5
 max_wal_size                        | 93
 max_worker_processes                | 8
[...]
{% endhighlight %}

The sames functions are provided to know what settings have been overloaded for
a specific user and/or database (the **ALTER ROLE ... SET**, **ALTER ROLE ...
IN DATABASE ... SET** and **ALTER DATABASE ... SET** commands), with the
functions:

  - pg\_track\_db\_role\_settings\_diff()
  - pg\_track\_db\_role\_settings\_log()
  - pg\_track\_db\_role\_settings()

And finally, just in case you can also know when PostgreSQL has been restarted:

{% highlight sql %}
postgres=# SELECT * FROM pg_reboot;
              ts
-------------------------------
 2015-07-17 08:39:37.315131+02
(1 row)
{% endhighlight %}

That's all for this extension.  I hope you'll never miss or forget a
configuration change again!

If you want to install it, the source code is available on the github
repository
[github.com/rjuju/pg\_track\_settings](https://github.com/rjuju/pg_track_settings).

### Limitations

As the only way to know what is the current value for a setting is to query
pg\_settings (or call current\_setting()), you must be aware that the user
calling **pg\_track\_settings\_snapshot()** may see an overloaded value (like
ALTER ROLE ... SET param = value) rather than the original value.  As the
**pg\_db\_role\_setting** table is also historized, it's pretty easy to know
that you don't see the original value, but there's no way to know **what** the
original value really is.
