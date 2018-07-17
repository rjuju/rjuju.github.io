---
layout: post
title: "pg_stat_kcache 2.1 is out"
modified:
categories: postgresql
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: gb
image:
  feature:
date: 2018-07-17T19:34:13+02:00
---

A new version of [pg_stat_cache](https://github.com/powa-team/pg_stat_kcache/)
is out, with support for widows and other platforms, and more counters
available.

### What's new

Version 2.1 of [pg_stat_cache](https://github.com/powa-team/pg_stat_kcache/)
has just been released.

The two main new features are:

* compatibility with platform without `getrusage()` support (such as Windows)
* more fields of `getrusage()` are exposed

As I explained in [a previous article]({% post_url
blog/2015-03-04-pg_stat_kcache-2-0 %}), this extension is a wrapper on top of
[getrusage](http://man7.org/linux/man-pages/man2/getrusage.2.html), that
accumulates performance counters per normalized query.  It was already giving
some precious informations that allows a DBA to identify CPU-intensive queries,
or compute a real hit-ratio for instance.

However, it was only available on platforms that have a native version
`getrusage`, so Windows and some other platforms were not supported.  But
fortunately, PostgreSQL does offer a [basic support of
`getrusage()`](https://github.com/postgres/postgres/blob/master/src/port/getrusage.c)
for those platforms.  This infrastructure has been used in the version 2.1.0 of
pg\_stat\_kcache, which means that you can now use this extension on Windows
and all the other platforms that wasn't supported previously.  As this is a
limited support, only the user and system CPU metrics will be available, the
other fields will always be NULL.

This new version also exposes all the remaining fields of `getrusage()` that
have a sense when accumulated per query:

* soft page faults
* hard page faults
* swaps
* IPC messages sent and received
* signals received
* voluntary and involuntary context switches

Another change is to automatically detect the operating system's clock tick.
Otherwise, very short queries (faster than a clock tick) would be either
detected as not consuming CPU time, or consuming CPU time from earlier short
queries.  For queries faster than 3 clock ticks, where imprecision is high,
pg\_stat\_kcache will instead use the query duration as CPU user time, and
won't use anything as CPU system time.

### Small example

Depending on your platform, some of those new counters aren't maintained.  On
GNU/Linux for instance , the swaps, IPC messages and signaled are unfortunately
not maintained, but those which are are still quite interesting.  For instance,
let's compare the `context switches` if we run the same number of total
transaction but with either 2 or 80 concurrent connections on a 4 core machine:

{% highlight bash %}
psql -c "SELECT pg_stat_kcache_reset()"
pgbench -c 80 -j 80 -S -n pgbench -t 100
[...]
number of transactions actually processed: 8000/8000
latency average = 8.782 ms
tps = 9109.846256 (including connections establishing)
tps = 9850.666577 (excluding connections establishing)

psql -c "SELECT user_time, system_time, minflts, majflts, nvcsws, nivcsws FROM pg_stat_kcache WHERE datname = 'pgbench'"
     user_time     |    system_time     | minflts | majflts | nvcsws | nivcsws
-------------------+--------------------+---------+---------+--------+---------
 0.431648000000005 | 0.0638690000000001 |   24353 |       0 |     91 |     282
(1 row)

psql -c "SELECT pg_stat_kcache_reset()"
pgbench -c 2 -j 2 -S -n pgbench -t 8000
[...]
number of transactions actually processed: 8000/8000
latency average = 0.198 ms
tps = 10119.638426 (including connections establishing)
tps = 10188.313645 (excluding connections establishing)

psql -c "SELECT user_time, system_time, minflts, majflts, nvcsws, nivcsws FROM pg_stat_kcache WHERE datname = 'pgbench'"
     user_time     | system_time | minflts | majflts | nvcsws | nivcsws 
-------------------+-------------+---------+---------+--------+---------
 0.224338999999999 |    0.023669 |    5983 |       0 |      0 |       8
(1 row)
{% endhighlight %}

As expected, having 80 concurrent connections on a 4 cores laptop is not the
most efficient way to process 8000 transactions.  The transactions latency is
**44 times** slower with 80 connections than with 2 connections.  At the O/S
level, we can see that with only 2 concurrent connections, we had only **8
involuntary context switches** on all queries on the **pgbench** database,
while there were **282, so 35 times more** with 80 concurrent connections.

Those new metrics give a lot more information of what's happening at the O/S
level, on a per normalized query granularity, and will ease diagnostic of
performance issues.  Combined with [PoWA](https://powa.readthedocs.io/), you'll
even be able to identify when any of those metrics have a different behavior!
