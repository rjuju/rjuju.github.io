---
layout: post
title: "Queryid reporting in plpgsql_check"
modified:
categories: postgresql
excerpt:
tags: [postgresql, performance, new_feature]
lang: gb
image:
  feature:
date: 2020-11-17T10:42:33+08:00
---

plpgsql_check version 1.14.0 was just released and brings some improvement for
performance diagnostic.

Thanks **a lot** to [Pavel Stěhule](http://okbob.blogspot.com/) for the awesome
plpgsql_check extension and the help for implementing the queryid reporting in
v1.14!

### plpgsql_check: static code analysis and more

PostgreSQL supports procedural code for many languages, the most popular one
probably being plpgsql.

Even if that language allows you to write raw SQL statements, any function
written in that language is still a block box as far as PostgreSQL in
concerned, which means that PostgreSQL won't perform a lot of checks to verify
code quality, typo or any other problem related to code development.  That's
where [plpgsql_check extension](https://github.com/okbob/plpgsql_check) comes
into play.

If you write any plpgsql code, this extension will be your best friend, as it
brings so many cool features.  The major feature is static code analysis, which
can detect many bugs, security / SQL inject issue and even possible performance
issue by detecting implicit casts that could prevent PostgreSQL from using
indexes and much more.

It also brings a simple, but yet very useful, **code profiler**.

### How to track down performance issue in plpgsql code?

As I mentioned above, plpgsql code is a black box as far as PostgreSQL is
concerned.  The direct consequence is that the performance diagnostic
possibilities are quite limited.

Using core PostgreSQL, the only option is using `pg_stat_user_functions` (wich
requires `track_functions` to be set to **pl** or **all**).  It'll show the
number of time each function has been called, and how long the execution took
including and excluding nested functions.  Unfortunately, this view can only
help you track you down **which** function is slow, but not **why**, as you
don't get any per-instruction metric.

You can somehow work around that limitation using the contrib extensions
[pg_stat_statements](https://www.postgresql.org/docs/current/pgstatstatements.html).
This extensions is one of the most popular one as far as performance diagnostic
is concerned, and gives you a lot of data on query performance (including
[planning counters]({% post_url
blog/2020-04-04-new-in-pg13-monitoring-query-planner %}) and [WAL counters]({%
post_url blog/2020-04-07-new-in-pg13-WAL-monitoring %}) since PostgreSQL 13).

The only problem is that it can be quite tricky to match pg_stat_statements
entries with your plpgsql code, as there's way to directly identify what
queries are run inside you plpgsql code.

### plpgsql_check code profiler

Another alternative is to use a plpgsql code profiler.  There are multiple
extensions that bring this feature, and I personally chose
[plpgsql_check](https://github.com/okbob/plpgsql_check), as it perfectly suited
my need: simple to setup and use, all performance information I needed and
possibility to use it either in a per-connection base or globally when
configuration the extension in **shared_preload_libraries**.  Thanks to this
profiler, you can finally get performance metrics at the statement level
**inside plpgsql code**:

- total execution time, that is the cumulated execution time for all the
  statements in the source code line
- average execution time, that is the total execution time divided by the
  number of statements in the source code line
- maximum execution time, per statement
- number of rows processed, per statement

With those information, it becomes quite easy to track down the slow part of
your functions.  Here's a simplistic example:

{% highlight sql %}
=# SELECT lineno, cmds_on_row, total_time, avg_time, max_time, source
  FROM plpgsql_profiler_function_tb('pltest()');
 lineno | cmds_on_row | total_time | avg_time |     max_time     |                        source
--------+-------------+------------+----------+------------------+-------------------------------------------------------
      1 |      <NULL> |     <NULL> |   <NULL> | <NULL>           |
      2 |      <NULL> |     <NULL> |   <NULL> | <NULL>           | DECLARE
      3 |      <NULL> |     <NULL> |   <NULL> | <NULL>           |     num bigint;
      4 |      <NULL> |     <NULL> |   <NULL> | <NULL>           |     _tbl text = 'pg_class';
      5 |           1 |      0.085 |    0.085 | {0.085}          | BEGIN
      6 |           1 |      0.504 |    0.504 | {0.504}          |     drop table if exists meh;
      7 |           1 |       0.81 |     0.81 | {0.81}           |     CREATE TABLE meh(id integer);
      8 |           1 |      0.362 |    0.362 | {0.362}          |     EXECUTE 'SELECT COUNT(*) FROM ' || _tbl INTO num;
      9 |           2 |    1000.84 |   500.42 | {0.349,1000.491} |     delete from meh; PERFORM pg_sleep(1);
     10 |           1 |          0 |        0 | {0}              |     RETURN num;
     11 |      <NULL> |     <NULL> |   <NULL> | <NULL>           | END;
(11 rows)
{% endhighlight %}

In this example, we can see immediately that the slowdown comes from source
code line n°9, which has a total execution time of 1s.  Using the **max_time**
field, wee see that it's due to the 2nd statements.  As we also have the source
code available in the view, we can immediately see the problematic query, which
here is a simple call to `pg_sleep(1)`.

So far so good.  But with less naive example the cause of slow execution might
be less obvious, and it could be handy to rely on all the available extensions
to get more information:
[pg_stat_statements](https://www.postgresql.org/docs/current/pgstatstatements.html)
for general counters,
[pg_stat_kcache](https://github.com/powa-team/pg_stat_kcache) for CPU and disk
usage counters,
[pg_wait_sampling](https://github.com/postgrespro/pg_wait_sampling) for wait
events and so on.

But how to match the plpgsql statement with entries in those extensions?

### Exposing queryid in plpgql_check profiler

Indeed, those extensions identify queries using a **query identifier**,
computed by **pg_stat_statements**.  You could try to manually find the related
entry using the query text stored by **pg_stat_statements**, but it may not
always be possible.  What if the query is dynamic SQL or using unqualified
names?

The solution here is quite simple: since plpgsql_check profiler already show
per-statement information, also report the statement's underlying queryid.

This is now available with version 1.14.0.  Using the previous naive example,
here's what we now see:

{% highlight sql %}
=# SELECT lineno, max_time, queryids, source
  FROM plpgsql_profiler_function_tb('pltest()');
 lineno |     max_time     |                 queryids                  |                        source
--------+------------------+-------------------------------------------+-------------------------------------------------------
      1 | <NULL>           | <NULL>                                    |
      2 | <NULL>           | <NULL>                                    | DECLARE
      3 | <NULL>           | <NULL>                                    |     num bigint;
      4 | <NULL>           | <NULL>                                    |     _tbl text = 'pg_class';
      5 | {0.085}          | <NULL>                                    | BEGIN
      6 | {0.504}          | {NULL}                                    |     drop table if exists meh;
      7 | {0.81}           | {NULL}                                    |     CREATE TABLE meh(id integer);
      8 | {0.362}          | {-7484655548452190292}                    |     EXECUTE 'SELECT COUNT(*) FROM ' || _tbl INTO num;
      9 | {0.349,1000.491} | {8162364748417812595,6729783856403017864} |     delete from meh; PERFORM pg_sleep(1);
     10 | {0}              | <NULL>                                    |     RETURN num;
     11 | <NULL>           | <NULL>                                    | END;
(11 rows)
{% endhighlight %}

You're now only a JOIN away from matching your plpgsql profile data from your
favorite extensions!

### Limitations

There are unfortunately some limitations.

Due to pg_stat_statements implementation, queryid for DDL queries is not
exposed outside the extension, so plpgsql_check can't retrieve it.

When using dynamic SQL, there might be **many** queries involved:

* the query text itself will be generated using SQL statement(s)
* the parameters, if any, will also be resolved running SQL statement(s)
* if the query text depends on some parameters, you can end up with multiple
  different top level query

plpgsql_check will only report the top level query identifier, and if multiple
different queries are generated only the query identifier of the first one will
be reported.

Even with those limitations I still hope that this new feature will be helpful.

### What's next?

Due to current plpgsql implementation, when a dynamic SQL statement is executed
the query identifier is not visible outside plpgsql itself.  It means that
retrieving the query identifier in that case is a bit costly, as plpgsql_check
has to do some of the work that plpgsql is doing:

* generate the final query string
* parse the query string
* call the parse analysis step (this is where the query identifier is
  generated)

Of course the query itself won't be executed or even planned, but those extra
steps might add non negligible overhead, especially when the dynamic SQL is
executing very short OLTP-style queries.

So plpgsql should be modified to be able to report the query identifier of all
statements, whether static or dynamic, so external modules can access the
information easily and without any additional overhead.  Ideally, this could
also be available in plpgsql code using a **GET [ CURRENT ] DIAGNOSTICS**
command, so users can also use it as they need.
