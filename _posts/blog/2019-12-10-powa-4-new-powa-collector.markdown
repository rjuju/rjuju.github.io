---
layout: post
title: "PoWA 4: New powa-collector daemon"
modified:
categories: postgresql
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: gb
image:
  feature:
date: 2019-12-10T19:54:17+01:00
---

This article is part of the [PoWA 4 beta](http://powa.readthedocs.io/) series,
and describes the new [powa-collector
daemon](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html).

### New [powa-collector daemon](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)

This daemon replaces the previous *background worker* when using the [new
remote mode](https://powa.readthedocs.io/en/latest/remote_setup.html).  It's a
simple daemon written in python, which will perform all the required steps to
perform *remote snapshots*.  It's [available on
pypi](https://pypi.org/project/powa-collector/).

As I explained in my [previous article introducing PoWA 4]({% post_url
blog/2019-05-17-powa-4-with-remote-mode-beta-is-available %}), this daemon is
required for a remote mode setup, with this architecture in mind:

<img src="/images/powa_4_remote.svg">

Its configuration is very simple.  All you need to do is copy and rename the
provided `powa-collector.conf.sample` file, and adapt the [connection
URI](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING)
to describe how to connect on your dedicated *repository server*, and you're
done.

A typical configuration will look like:

{% highlight conf %}
{
    "repository": {
        "dsn": "postgresql://powa_user@server_dns:5432/powa",
    },
    "debug": true
}
{% endhighlight %}

The list of *remote servers*, their configuration and everything else it needs
will be automatically retrieved from the *repository server* you just
configured.  When started, it'll spawn one dedicated thread per declared
*remote server*, and maintain a **persistent connection** on the configured
**powa database** on this *remote server*.  Each thread will perform a *remote
snapshot*, exporting the data on the *repository server* using the new *source
functions*.  Each thread will open and close a connection on the *repository
server* when performing the *remote snapshot*.

This daemon obviously needs to be able to connect to all the declared *remote
servers* and the *repository server*.  The `powa_servers` table, which store
the list of *remote servers*,  has a field to store username and password to
connect to the *remote server*.  Storing a password in plain text in this table
is an heresy as far as security is concerned.  So, as mentioned in the
[PoWA security
documentation](https://powa.readthedocs.io/en/latest/security.html#connection-on-remote-servers),
you can store a NULL password and [instead use any of the authentication method
that libpq supports](https://www.postgresql.org/docs/current/auth-methods.html)
(.pgpass file, certificate...).  That's strongly recommended for any non toy
setup.

The persistent connection on the *repository server* is used to monitor the
daemon:

  * to check that the daemon is up and running
  * to communicate through the UI using a [simple protocol](https://powa.readthedocs.io/en/latest/components/powa-collector/protocol.html)
    to perform various actions (reload the configuration, check for a *remote
    server* thread status...)

Note that you can also ask the daemon to reload its configuration by issuing a
SIGHUP to the daemon process.  A reload is required if any modification to the
list of remote servers (if you added or removed a *remote server*, or
updated a setting for an existing) has been done.

Also note that by choice,
[powa-collector](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
will not perform *local snapshots*.  If you want to use PoWA for the
*repository server*, you need to enable the original *background worker*.

##### New configuration page

The configuration page is now updated to give all needed information about the
background worker status and the [powa-collector
daemon](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
status (including all of its dedicated threads) and the list of registered
*remote servers*.  Here's an example of the new root configuration page:

<img src="/images/powa_4_configuration_page.png">

If the [powa-collector
daemon](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
is used, each remote server status will be retrieved using the communication
protocol.  If the collector encountered any error (connecting to a *remote
server*, during a *snapshot* or anything else), they'll also be displayed here.
Also note that such errors will also be displayed on top of any page of the UI,
so that you can't miss them.

Also, the configuration section has now a hierarchy, and you'll be able to see
the list of extensions and the current PostgreSQL configuration for the
**local** or **remote servers** by clicking on the server of your choice!

There's also a new **Reload collector** button on the header panel, which as
expected will ask the collector to reload its configuration.  That can be
useful if you registered new servers and you don't have access on the server
where the collector is running.

### Conclusion

This is the last article introducing the new version of PoWA.  It's still in
beta, so feel free to test it, [report any issue you may
find](https://powa.readthedocs.io/en/latest/support.html#support) or give any
other feedback!
