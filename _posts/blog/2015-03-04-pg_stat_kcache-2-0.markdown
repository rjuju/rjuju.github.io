---
layout: post
title: "pg_stat_kcache 2.0"
modified:
categories: postgresql
excerpt:
tags: [ postgresql, monitoring, PoWA]
image:
  feature:
date: 2015-03-04T19:33:09+01:00
---

### Some history

My colleague [Thomas](https://github.com/frost242) created the first version of [pg_stat_kcache](https://github.com/dalibo/pg_stat_kcache) about a year ago. This extension is based on [getrusage](http://linux.die.net/man/2/getrusage), which provides some useful metrics, not available in PostgreSQL until now:

* CPU usage (user and system)
* Disk access (read and write)

PostgreSQL already has its own wrapper around getrusage (see [pg_rusage.c](https://github.com/postgres/postgres/blob/master/src/backend/utils/misc/pg_rusage.c)), but it's only used in a few places like VACUUM/ANALYZE execution statistics, only to display CPU usage and execution time, that wasn't enough for our need.

The first version of the extension gave access to these metrics, but only with the granularity of the query operation (SELECT, UPDATE, INSERT...). It was interesting but still not enough. However, that's all that could be done with the existing infrastructure.

But then, this patch is committed : [Expose qurey ID in pg_stat_statements view.](https://github.com/postgres/postgres/commit/91484409bdd17f330d10671d388b72d4ef1451d7). That means that, starting with PostgreSQL 9.4, we now have a way to aggregate statistic **per query, database and user**, as long as [pg_stat_statements](http://www.postgresql.org/docs/current/static/pgstatstatements.html) is installed, which is far more useful. That's what the new version 2.0 of pg_stat_statements is all about.

### Content

As I said just before, this version of pg_stat_kcache relies on pg_stat_statements:

{% highlight sql %}
# CREATE EXTENSION pg_stat_kcache ;
ERROR:  required extension "pg_stat_statements" is not installed
# CREATE EXTENSION pg_stat_statements ;
CREATE EXTENSION
# CREATE EXTENSION pg_stat_kcache ;
CREATE EXTENSION
# \dx
                                     List of installed extensions
        Name        | Version |   Schema   |                        Description
--------------------+---------+------------+-----------------------------------------------------------
 pg_stat_kcache     | 2.0     | public     | Kernel cache statistics gathering
 pg_stat_statements | 1.2     | public     | track execution statistics of all SQL statements executed
 plpgsql            | 1.0     | pg_catalog | PL/pgSQL procedural language
(3 rows)

{% endhighlight %}

What does the extension provide ?

{% highlight sql %}
# \dx+ pg_stat_kcache
Objects in extension "pg_stat_kcache"
       Object Description
---------------------------------
 function pg_stat_kcache()
 function pg_stat_kcache_reset()
 view pg_stat_kcache
 view pg_stat_kcache_detail
(4 rows)
{% endhighlight %}


There are two functions:

* **pg_stat_kcache()**: returns the metric values, grouped by query, database and user.
* **pg_stat_kcache_reset()**: reset the metrics.

And two views on top of the first function:

* **pg_stat_kcache**: provide the metrics, aggregated by database only
* **pg_stat_kcache_detail**: provide the same information as the **pg_stat_kcache()**
function, but with the actual query text, database and user names.

Here are the units:

* reads: in **bytes**
* reads_blks: raw output of getursage, unit is **512bits** on linux
* writes: in **bytes**
* writes_blks: raw output of getursage, unit is **512bits** on linux
* user_time: in **seconds**
* system_time: in **seconds**

### Usage

So now, let's see in detail all this stuff.

Let's first generate some activity to see all that counters going up:

{% highlight sql %}
(postgres@127.0.0.1:59412) [postgres]=# CREATE TABLE big_table (id integer, val text);
CREATE TABLE

\timing
# INSERT INTO big_table SELECT i, repeat('line ' || i,50) FROM generate_series(1,1000000) i;
INSERT 0 1000000
Time: 62368.157 ms

# SELECT i,md5(concat(i::text,md5('line' || i))) FROM generate_series(1,1000000) i;
[...]
Time: 5135.980 ms

{% endhighlight %}

Which gives us:
{% highlight sql %}
# \x
# SELECT * FROM pg_stat_kcache_detail;
-[ RECORD 1]---------------------------------------------------------------------------------
 query       | INSERT INTO big_table SELECT i, repeat(? || i,?) FROM generate_series(?,?) i;
 datname     | kcache
 rolname     | rjuju
 reads       | 0
 reads_blks  | 0
 writes      | 933814272
 writes_blks | 107753
 user_time   | 7.592
 system_time | 0.86
-[ RECORD 2]---------------------------------------------------------------------------------
 query       | SELECT i,md5(concat(i::text,md5(? || i))) FROM generate_series(?,?) i;
 datname     | kcache
 rolname     | rjuju
 reads       | 0
 reads_blks  | 0
 writes      | 14000128
 writes_blks | 1709
 user_time   | 5.032
 system_time | 0.088
[...]
{% endhighlight %}

The INSERT query had a runtime of about 1 minute. We see that it used 7.6s of CPU, and wrote 890 MB on disk. Without any surprise, this query is I/O bound.

The SELECT query had a runtime of 5.1s, and it consumed 5s of CPU time. As expected, using md5() is CPU expensive, to the bottleneck here is the CPU. Also, we see that this query wrote 14000128 bytes. Why would a simple SELECT query without any aggregate would write 13MB on disk ? Yes, the answer is geneate_series(), which use a temporary file if the data don't fit in work_mem:

{% highlight sql %}
# SHOW work_mem ;
 work_mem
----------
 10MB

# EXPLAIN (analyze,buffers) SELECT * FROM generate_series(1,1000000);
                                                         QUERY PLAN
----------------------------------------------------------------------------------------------------------------------------
 Function Scan on generate_series  (cost=0.00..10.00 rows=1000 width=4) (actual time=253.849..462.864 rows=1000000 loops=1)
   Buffers: temp read=1710 written=1709
 Planning time: 0.050 ms
 Execution time: 548.298 ms

-- How many bytes are 1709 blocks ?
# SELECT 1709 * 8192;
 ?column?
----------
 14000128
(1 row)

Time: 0.753 ms

{% endhighlight %}

And we find the exact amount of writes :)

### Going further

As we now have the number of bytes physically read from disk, and pg_stat_statements provides the bytes read on shared_buffers, read outside the shared_buffers and written, we can compute many things, like:

* an exact hit-ratio, meaning having :
  * what was read from the shared_buffers
  * what was read in the filesystem cache
  * what was read from disk

And, thanks to pg_stat_statements, we can compute this exact hit-ratio per query and/or user and/or database!

For instance, getting these metrics on all databases on a server:
{% highlight sql %}
# SELECT datname, query,
shared_hit *100 / int8larger(1,shared_hit + shared_read) as shared_buffer_hit,
(shared_read - reads) *100 / int8larger(1,shared_hit + shared_read) as system_cache_hit,
reads *100 / int8larger(1,shared_hit + shared_read) as physical_disk_read
FROM (SELECT userid, dbid, queryid, query, shared_blks_hit * 8192 as shared_hit, shared_blks_read * 8192 AS shared_read FROM pg_stat_statements) s
JOIN pg_stat_kcache() k USING (userid, dbid, queryid)
JOIN pg_database d ON s.dbid = d.oid
ORDER BY 1,2
{% endhighlight %}

Or getting the 5 most I/O writes consuming queries per database:
{% highlight sql %}
# SELECT datname, query, writes
FROM (
    SELECT datname, query, writes, row_number() OVER (PARTITION BY datname ORDER BY writes DESC) num
    FROM pg_stat_statements s
    JOIN pg_stat_kcache() k USING (userid, dbid, queryid)
    JOIN pg_database d ON s.dbid = d.oid
) sql
WHERE num <= 5
ORDER BY 1 ASC, 3 DESC
{% endhighlight %}


As you can see, this new extension is really helpful to have a lot of informations about physical resources consumption on a PostgreSQL server, which wasn't possible to retrieve before.

But you'll get much more if you use it with [PoWA](https://dalibo.github.io/powa), as it will gather all the required informations periodically, and will do all the maths to show you nice graphs and charts to ease the interpretation of all these metrics.

It mean that you'll have all these informations, sampled on a few minutes interval. So, knowing which queries use the most CPU between 2 and 3 AM will just be a few clicks away from you.

If you want to take a look a this interface, you can check out the offical demo, at [http://demo-powa.dalibo.com](http://demo-powa.dalibo.com), powa // demo.

Have fun!
