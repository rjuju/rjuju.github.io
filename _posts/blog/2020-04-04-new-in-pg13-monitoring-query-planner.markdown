---
layout: post
title: "New in pg13: Monitoring the query planner"
modified:
categories: postgresql
excerpt:
tags: [postgresql, monitoring, pg13, new_feature]
lang: gb
image:
  feature:
date: 2020-04-04T14:06:15+02:00
---

Depending on your workload, the planning time can represent a significant part
of the overal query procesing time.  This is especially import in OLTP
workload, but OLAP queries with numerous tables being joined and an aggressive
configuration on the JOIN order search can also lead to hight planning time.

### Planning counters in pg_stat_statements

Previously, pg\_stat\_statements was only keeping track of the execution part
of a query processing: the number of execution, cumulated time, but also
minimum, maximum, mean and also the standard deviation.  With PostgreSQL 13,
you'll also have those metrics for the planification part!

{% highlight commit %}
    commit 17e03282241c6ac58a714eb0c3b6a8018cf6167a
    Author: Fujii Masao <fujii@postgresql.org>
    Date:   Thu Apr 2 11:20:19 2020 +0900

        Allow pg_stat_statements to track planning statistics.

        This commit makes pg_stat_statements support new GUC
        pg_stat_statements.track_planning. If this option is enabled,
        pg_stat_statements tracks the planning statistics of the statements,
        e.g., the number of times the statement was planned, the total time
        spent planning the statement, etc. This feature is useful to check
        the statements that it takes a long time to plan. Previously since
        pg_stat_statements tracked only the execution statistics, we could
        not use that for the purpose.

        The planning and execution statistics are stored at the end of
        each phase separately. So there are not always one-to-one relationship
        between them. For example, if the statement is successfully planned
        but fails in the execution phase, only its planning statistics are stored.
        This may cause the users to be able to see different pg_stat_statements
        results from the previous version. To avoid this,
        pg_stat_statements.track_planning needs to be disabled.

        This commit bumps the version of pg_stat_statements to 1.8
        since it changes the definition of pg_stat_statements function.

        Author: Julien Rouhaud, Pascal Legrand, Thomas Munro, Fujii Masao
        Reviewed-by: Sergei Kornilov, Tomas Vondra, Yoshikazu Imai, Haribabu Kommi, Tom Lane
        Discussion: https://postgr.es/m/CAHGQGwFx_=DO-Gu-MfPW3VQ4qC7TfVdH2zHmvZfrGv6fQ3D-Tw@mail.gmail.com
        Discussion: https://postgr.es/m/CAEepm=0e59Y_6Q_YXYCTHZkqOc6H2pJ54C_Xe=VFu50Aqqp_sA@mail.gmail.com
        Discussion: https://postgr.es/m/DB6PR0301MB21352F6210E3B11934B0DCC790B00@DB6PR0301MB2135.eurprd03.prod.outlook.com
{% endhighlight %}


Keep in mind that even simple query can have a surprisingly high planification
time.  One of the frequent cause was the `get_actual_variable_range()`
function, which is called when the planner wants to know what are the minimum
and maximum values of a specific field.  This function detects if a suitable
index exists, and if there's one it gets the wanted values.  However, when
there were a lot of uncommitted values at the end of the index range, it could
take a significant amount of time to get a visible value.  While this problem
has been fixed long ago (see [this
commit](https://github.com/postgres/postgres/commit/fccebe421d0c410e6378fb281419442c84759213)
and [this other
commit](https://github.com/postgres/postgres/commit/3ca930fc39ccf987c1c22fd04a1e7463b5dd0dfd)
for more details), there are still some cases where the planning time is higher
than what you'd expect, so having an easy way to monitor the planification
metrics is worthwhile.

This feature can also be interesting to know how much you're using the [generic
plan feature](https://www.postgresql.org/docs/current/sql-prepare.html) for
instance, and how much of a difference this should make for instance.

Let's see a simple example, to see the effect of generic plans with prepared
statements:

{% highlight sql %}
=# PREPARE s1 AS SELECT count(*) FROM pg_class;
PREPARE
=# EXECUTE s1;
 count
-------
   387
(1 row)

[... 5 more times ...]

=# SELECT query, plans, total_plan_time, total_plan_time / plans AS avg_plan,
   calls, total_exec_time, total_exec_time / calls AS avg_exec
   FROM pg_stat_statements
   WHERE query ILIKE '%SELECT count(*) FROM pg_class%';
-[ RECORD 1 ]---+--------------------------------------------
query           | PREPARE s1 AS SELECT count(*) FROM pg_class
plans           | 1
total_plan_time | 2.119496
avg_plan        | 2.119496
calls           | 6
total_exec_time | 3.4918280000000004
avg_exec        | 0.5819713333333334
{% endhighlight %}

While the query was executed 6 times, it was actually planned only once (since
there's no parameter, a generic plan is always used).  While the execution time
is on average slightly more than half a milliscond, a single planning was
almost **4 times** more expensive.  By saving 5 planification, postgres saved
up to **10ms**.

### Planning buffers in EXPLAIN

    commit ce77abe63cfc85fb0bc236deb2cc34ae35cb5324
    Author: Fujii Masao <fujii@postgresql.org>
    Date:   Sat Apr 4 03:13:17 2020 +0900

        Include information on buffer usage during planning phase, in EXPLAIN output, take two.

        When BUFFERS option is enabled, EXPLAIN command includes the information
        on buffer usage during each plan node, in its output. In addition to that,
        this commit makes EXPLAIN command include also the information on
        buffer usage during planning phase, in its output. This feature makes it
        easier to discern the cases where lots of buffer access happen during
        planning.

        This commit revives the original commit ed7a509571 that was reverted by
        commit 19db23bcbd. The original commit had to be reverted because
        it caused the regression test failure on the buildfarm members prion and
        dory. But since commit c0885c4c30 got rid of the caues of the test failure,
        the original commit can be safely introduced again.

        Author: Julien Rouhaud, slightly revised by Fujii Masao
        Reviewed-by: Justin Pryzby
        Discussion: https://postgr.es/m/16109-26a1a88651e90608@postgresql.org


Following the same idea, EXPLAIN will now display the buffer usage if the
`BUFFERS` option is used.  If you try that on a fresh new connection, before
any catalog cache is populated, you could be surprised on how many buffers
would be accessed even for a simple query:

{% highlight sql %}
=# EXPLAIN (BUFFERS, ANALYZE, COSTS OFF) SELECT * FROM pg_class;
                                               QUERY PLAN
---------------------------------------------------------------------------------------------------------
 Seq Scan on pg_class (actual time=0.028..0.410 rows=388 loops=1)
   Buffers: shared hit=13
 Planning Time: 5.157 ms
   Buffers: shared hit=118
 Execution Time: 1.257 ms
(5 rows)

=# EXPLAIN (BUFFERS, ANALYZE, COSTS OFF) SELECT * FROM pg_class;
                            QUERY PLAN
------------------------------------------------------------------
 Seq Scan on pg_class (actual time=0.035..0.413 rows=388 loops=1)
   Buffers: shared hit=13
 Planning Time: 0.393 ms
 Execution Time: 0.670 ms
{% endhighlight %}

We can see here that populating the cache (relation, columns, datatypes...)
access 118 blocks, and that's probably a significant part of the 5 extra ms we
saw in the first EXPLAIN output.
