---
layout: post
title: "Planner selectivity estimation error statistics with pg_qualstats 2"
modified:
categories: postgresql
excerpt:
tags: [ postgresql, monitoring, performance]
lang: gb
image:
  feature:
date: 2020-02-28T13:37:04+01:00
---

Selectivity estimation error is one of the main cause of bad query plans.  It's
quite straighforward to compute those estimation error using `EXPLAIN
(ANALYZE)`, either manually or with the help of
[explain.depesz.com](https://explain.depesz.com/) (or other similar tools),
but until now there were now tool available to get this information
automatically and globally.  Version 2 of pg\_qualstats fixes that, thanks a
lot to [Oleg Bartunov](https://twitter.com/obartunov) for the original idea!

Note: If you don't know pg\_qualstats extension, you may want to see [my last
article about it]({% post_url
blog/2020-01-06-pg_qualstats-2-global-index-advisor %}).

### The problem

There can be many causes to that issue: outdated statistics, complex
predicates, non uniform data...  But whatever the reason is, if the optimizer
doesn't have an accurate idea on how much data each predicate will filter, the
result is the same: a bad query plan, which can lead to longer query execution.

To illustrate the problem, I'll use here a simple test case, voluntarily built
to fool the optimizer.

{% highlight sql %}
rjuju=# CREATE TABLE pgqs AS
             SELECT  i%2 val1 , (i+1)%2 val2
             FROM generate_series(1, 50000) i;
SELECT 50000

rjuju=# VACUUM ANALYZE pgqs;
VACUUM

rjuju=# EXPLAIN (ANALYZE) SELECT * FROM pgqs WHERE val1 = 1 AND val2 = 1;
                             QUERY PLAN
--------------------------------------------------------------------
 Seq Scan on pgqs  ([...] rows=12500 width=8) ([...] rows=0 loops=1)
   Filter: ((val1 = 1) AND (val2 = 1))
   Rows Removed by Filter: 50000
 Planning Time: 0.553 ms
 Execution Time: 38.062 ms
(5 rows)
{% endhighlight %}

Here postgres think that the query will emit 12500 tuples, while in reality
none will be emitted.  If you're wondering how postgres came up with that
number, the explanation is simple.  When multiple independant (overlapping
range predicate can be merged) clauses are AND-ed and no extended statistics
are available (see below for more about it), postgres will simply multiply each
clause selectivity.  This is done in `clauselist_selectivity_simple`, in
[src/backend/optimizer/path/clausesel.c](https://github.com/postgres/postgres/blob/master/src/backend/optimizer/path/clausesel.c):

{% highlight c %}
Selectivity
clauselist_selectivity_simple(PlannerInfo *root,
                List *clauses,
                int varRelid,
                JoinType jointype,
                SpecialJoinInfo *sjinfo,
                Bitmapset *estimatedclauses)
{
  Selectivity s1 = 1.0;
  [...]
  /*
   * Anything that doesn't look like a potential rangequery clause gets
   * multiplied into s1 and forgotten. Anything that does gets inserted into
   * an rqlist entry.
   */
  listidx = -1;
  foreach(l, clauses)
  {
    [...]
    /* Always compute the selectivity using clause_selectivity */
    s2 = clause_selectivity(root, clause, varRelid, jointype, sjinfo);
    [...]
        /*
         * If it's not a "<"/"<="/">"/">=" operator, just merge the
         * selectivity in generically.  But if it's the right oprrest,
         * add the clause to rqlist for later processing.
         */
        switch (get_oprrest(expr->opno))
        {
          [...]
          default:
            /* Just merge the selectivity in generically */
            s1 = s1 * s2;
            break;
          [...]
{% endhighlight %}

In this case, each predicate will independantly filter approximately 50% of the
table, as we can see in **pg_stats view**:

{% highlight sql %}
rjuju=# SELECT tablename, attname, most_common_vals, most_common_freqs
        FROM pg_stats WHERE tablename = 'pgqs';
 tablename | attname | most_common_vals |    most_common_freqs
-----------+---------+------------------+-------------------------
 pgqs      | val1    | {0,1}            | {0.50116664,0.49883333}
 pgqs      | val2    | {1,0}            | {0.50116664,0.49883333}
(2 rows)
{% endhighlight %}

So when using both clauses, the estimate is 25% of the table, since postgres
doesn't know **by default** that both values are mutually exclusive.
Continuing with this artificial test case, let's see what happens if we add a
*join* on top of if.  For instance, joining the table to itself on the `val1`
column only.  For clarity, I'll use **t1** for the table on which I'm applying
the mutually exclusive predicates, and **t2** the table joined:

{% highlight sql %}
rjuju=# EXPLAIN ANALYZE SELECT *
        FROM pgqs t1
        JOIN pgqs t2 ON t1.val1 = t2.val1
        WHERE t1.val1 = 0 AND t1.val2 = 0;
                                     QUERY PLAN
-----------------------------------------------------------------------------------
 Nested Loop  ([...] rows=313475000 width=16) ([...] rows=0 loops=1)
   ->  Seq Scan on pgqs t2  ([...] rows=25078 width=8) ([...] rows=25000 loops=1)
         Filter: (val1 = 0)
         Rows Removed by Filter: 25000
   ->  Materialize  ([...] rows=12500 width=8) ([...] rows=0 loops=25000)
         ->  Seq Scan on pgqs t1  ([...] rows=12500 width=8) ([...] rows=0 loops=1)
               Filter: ((val1 = 0) AND (val2 = 0))
               Rows Removed by Filter: 50000
 Planning Time: 0.943 ms
 Execution Time: 86.757 ms
(14 rows)
{% endhighlight %}

Postgres thinks that this join will emit **313 millions rows**, while obviously
no rows will be emitted.  And this is a good example on how bad assumptions can
lead to an inefficient plan.

Here Postgres can deduce that the `val1 = 0` predicate can be applied to
**t2**.  So how to join two relations, one that should emit 25000 tuples and
the other that should emit 12500 tuples, with no index available?  A nested
loop is not a bad choice, as both relation aren't really big.  As no index is
available, postgres also chooses to **materialize** the inner relation, meaning
storing it in memory, to make it more efficient.  As it tries to limit memory
consumption as much as possible, the smallest relation is materialized, and
that's the mistake here.

Indeed, postgres will read the whole table twice: once to get every rows
corresponding to the `val1 = 0` predicate for the outer relation, and once to
find all rows to be materialized.  If the opposite was done, as it would
probably have if the estimates had been more realistic, the table would only
have been read once.

In this case, as the dataset isn't big and quite artificial, a better plan
wouldn't drastically change the execution time.  But keep in mind than with
real production environements, it could mean choosing a nested loop assuming
that there'll be only a couple of rows to loop on while in reality the backend
will spend minutes or even hours looping over millions of rows, and another
plan would have been orders of magnitude quicker.

### Detecting the problem

pg\_qualstats 2 will now compute the selectivity estimation error, both in a
ratio and a raw number, and will keep track for each predicate the minimum,
maximum and mean values, with the standard deviation.  This is now quite simple
to detect problematic quals!

After executing the last query, here's what the `pg_qualstats` view will
return:

{% highlight sql %}
rjuju=# SELECT relname, attname, opno::regoper, qualid, qualnodeid,
    mean_err_estimate_ratio mean_ratio, mean_err_estimate_num mean_num, constvalue
    FROM pg_qualstats pgqs
    JOIN pg_class c ON pgqs.lrelid = c.oid
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = pgqs.lattnum;
 relname | attname | opno |   qualid   | qualnodeid | mean_ratio | mean_num | constvalue
---------+---------+------+------------+------------+------------+----------+------------
 pgqs    | val1    | =    |     <NULL> | 3161070364 | 1.00393542 |       98 | 0::integer
 pgqs    | val1    | =    | 3864967567 | 3161070364 |      12500 |    12500 | 0::integer
 pgqs    | val2    | =    | 3864967567 | 3065200358 |      12500 |    12500 | 0::integer
(3 rows)
{% endhighlight %}

**NOTE:** `qualid` is an identifier if multiple qual are AND-ed, NULL
otherwise, and `qualnodeid` is a per-qual only identifier.
{: .notice}

We see here that when used alone, the qual `pgqs.val = ?` doesn't show any
selectivity estimate problem as the ratio (*mean\_ratio*) is very close to
**1** and the raw number (*mean\_num*) is quite low.  On the other hand, when
combined with `AND pgqs.val2 = ?` pg\_qualstats reports significant estimate
error.  That's a very strong sign that those columns are functionally
dependent.

If for example a qual alone shows issues, it could be a sign of outdated
statistics, or that the sample size isn't big enough.

Also, if you have `pg_stat_statements` extension installed, `pg_qualstats` will
give you the *query identifier* for each predicate.  With that and a bit of
SQL, you can for instance find the query with a long average execution time
which contains quals for which the selectivity estimation is off by 10 or more.

### Interlude: Extended statistics

If you're wondering how to solve the issue I just explained, the solution is
very easy since **extended statistics** were introduced in PostgreSQL 10, and
assuming that you know that's the root issue.  [Create an extended
statistcs](https://www.postgresql.org/docs/current/sql-createstatistics.html)
on the related columns, perform an ANALYZE and you're done!

{% highlight sql %}
rjuju=# CREATE STATISTICS pgqs_stats ON val1, val2 FROM pgqs;
CREATE STATISTICS

rjuju=# ANALYZE pgqs;
ANALYZE

rjuju]=# EXPLAIN ANALYZE SELECT *
        FROM pgqs t1
        JOIN pgqs t2 ON t1.val1 = t2.val1
        WHERE t1.val1 = 0 AND t1.val2 = 0 order by t1.val2;
                             QUERY PLAN
-------------------------------------------------------------------------
 Nested Loop  ([...] rows=25002 width=16) ([...] rows=0 loops=1)
   ->  Seq Scan on pgqs t1  ([...] rows=1 width=8) ([...] rows=0 loops=1)
         Filter: ((val1 = 0) AND (val2 = 0))
         Rows Removed by Filter: 50000
   ->  Seq Scan on pgqs t2  ([...] rows=25002 width=8) (never executed)
         Filter: (val1 = 0)
 Planning Time: 0.559 ms
 Execution Time: 39.471 ms
(8 rows)
{% endhighlight %}

If you want more details on extended statistics, I recommend looking at the
slides from [Tomas Vondra](https://blog.pgaddict.com/)'s [excellent talk on
this
subject](https://www.postgresql.eu/events/pgconfeu2018/sessions/session/2083/slides/130/create-statistics-what-is-it.pdf).

### Going further

Tracking the quals in every single qual executed is of course quite expensive,
and would significantly impact the performance for any non datawarehouse
workload.  That's why `pg_qualstats` has an option,
**pg_qualstats.sample_rate**,  to sample the query that will be processed.
This setting is by default set to **1 / max_connections**, which will make the
overhead quite negligible, but don't be surprised if you don't see any qual
reported after running a few queries!

But if you're instead only interested by the quals that has bad selectivity
estimation, for instance to detect this class of problem rather than missing
indexes, there are two new options available for that:

  * **pg_qualstats.min_err_estimate_ratio**
  * **pg_qualstats.min_err_estimate_num**

Those options are cumulative and can be changed at anytime, and will limit the
quals that pg\_qualstats will store to the ones that have a selectivity
estimate ratio and/or raw number higher that what you ask.  Although those
options will help to reduce the performance overhead, they of course can be
combined with **pg_qualstats.sample_rate** if needed.

### Conclusion

After [introducing the new global index advisor]({% post_url
blog/2020-01-06-pg_qualstats-2-global-index-advisor %}), this article presented
a class of problems that are frequently seen as a DBA, and how to detect and
solve them.

I believe that those two new features in pg\_qualstats will greatly help
PostgreSQL databases administration.  Also, external tools that aims to solve
related issue, such as
[pg_plan_advsr](https://github.com/ossc-db/pg_plan_advsr) or
[AQO](https://github.com/postgrespro/aqo) could also benefit from
pg\_qualstats, as they could directly get the exact data they need to be able
perform analysis and optimize the queries!
