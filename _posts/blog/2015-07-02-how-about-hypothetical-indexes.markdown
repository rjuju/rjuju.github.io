---
layout: post
title: "How About Hypothetical Indexes ?"
modified:
categories: postgresql
excerpt:
tags: [PoWA, performance, tuning, postgresql]
image:
  feature:
date: 2015-07-02T11:08:03+01:00
---

After so much time missing this features,
[HypoPG](https://github.com/dalibo/hypopg) implements hypothetical indexes
support for PostgreSQl, available as an extension.

### Introduction

It's now been some time since the second version of
[PoWA](https://dalibo.github.io/powa) has been announced. One of the new feature
of this version is the [pg\_qualstats](https://github.com/dalibo/pg_qualstats)
extension, written by [Ronan Dunklau](https://rdunklau.github.io).

Thanks to this extension, we can now gather real-time statistics to detect
missing indexes, and much more (if you're interested in this extension, you
should read [Ronan's article about
pg\_qualstats](http://rdunklau.github.io/postgresql/powa/pg_qualstats/2015/02/02/pg_qualstats_part1/)).
And used with PoWA, you have an interface that allows you to find the most
consuming queries, and will suggest you the missing indexes if they're needed.

That's really nice, but now a lot of people come with this natural question:
**Ok, you say that I should create this index, but will PostgreSQL eventually
use it ?**. That's actually a good question, because depending on many
parameters (in many other things), PostgreSQL could choose to just ignore your
freshly created index.  That could be a really bad surprise, especially if you
had to wait many hours to have it built.

### Hypothetical Indexes

So yes, the answer to this question is **hypothetical indexes support**. That's
really not a new idea, a lot of popular RDBMS support them.

There has already been some previous work on this several years ago, presented
at [pgCon 2010](http://www.pgcon.org/2010/schedule/events/233.en.html), which
was implementing much more than hypothetical indexes, but this was a research
work, which means that we never saw those features coming up in PostgreSQL.
This great work is only available as a fork of a few specific PostgreSQL
versions, the most recent being 9.0.1.

### lightweight implementation: HypoPG

I had quite a different approach in HypoPG to implement hypothetical indexes
support.

  * first of all, it must be completely pluggable. It's available as an
extension and can be used (for now) on any 9.2 or higher PostgreSQL server.
  * it must be as non intrusive as it's possible. It's usable as soon as you
create the extension, without restart. Also, each backend has its own set of
hypothetical indexes, which mean that adding an hypothetical index will not
disturb other connections. Also, the hypothetical indexes are stored in memory,
adding/removing a huge amount of them will not bloat your system catalog.

The only restriction in implementing such a feature as an extension is that you
can't change the syntax without modifying the PostgreSQL source code. So,
everything has to be done through user defined functions, and change regular
behaviour of existing functionnalities, like the EXPLAIN command. We'll study
the details later.

### Features

For now, the following functions are available:

  * **hypopg()**: return the list of hypothetical indexes (in a
similar way as pg\_index).
  * **hypopg\_add\_index(schema, table, attribute, access\_method)**: create a
1-column-only hypothetical index.
  * **hypopg\_create\_index(query)**: create an hypothetical index using a
standard CREATE INDEX statement.
  * **hypopg\_drop\_index(oid)**: remove the specified hypothetical index.
  * **hypopg\_list\_indexes()**: return a short human readable version list
of available hypothetical indexes.
  * **hypopg\_relation\_size(oid)**: return the estimated size of an
hypothetical index
  * **hypopg\_reset()**: remove all hypothetical indexes

If some hypothetical indexes exists for some relations used in an EXPLAIN
(without ANALYZE) statement, they will automatically be added to the list of
real indexes. PostgreSQL will then choose to use them or not.

### Usage

Installing HypoPG is quite simple. Assuming you downloaded and extracted a
tarball in the hypopg-0.0.1 directory, are using a packaged version of
PostgreSQL and have -dev packages:

{% highlight bash %}
$ cd hypopg-0.0.1
$ make
$ sudo make install
{% endhighlight %}

Then HypoPG should be available:

{% highlight sql %}
rjuju=# CREATE EXTENSION hypopg ;
CREATE EXTENSION
{% endhighlight %}

Let's try some really simple tests. First, create a small table:

{% highlight sql %}
rjuju=# CREATE TABLE testable AS SELECT id, 'line ' || id val
rjuju=# FROM generate_series(1,1000000) id;
SELECT 100000
rjuju=# ANALYZE testable ;
ANALYZE
{% endhighlight %}

Then, let's see a query plan that should benefit an index that's not here:

{% highlight sql %}
rjuju=# EXPLAIN SELECT * FROM testable WHERE id < 1000 ;
                          QUERY PLAN
---------------------------------------------------------------
 Seq Scan on testable  (cost=0.00..17906.00 rows=916 width=15)
   Filter: (id < 1000)
(2 rows)

{% endhighlight %}

No surprise, a sequential scan is the only way to go. Now, let's try to add
an hypothetical index, and EXPLAIN again:

{% highlight sql %}
rjuju=# SELECT hypopg_create_index('CREATE INDEX ON testable (id)');
 hypopg_create_index
---------------------
 t
(1 row)

Time: 0,753 ms

rjuju=# EXPLAIN SELECT * FROM testable WHERE id < 1000 ;
                                          QUERY PLAN
-----------------------------------------------------------------------------------------------
 Index Scan using <41079>btree_testable_id on testable  (cost=0.30..28.33 rows=916 width=15)
   Index Cond: (id < 1000)
(2 rows)
{% endhighlight %}

Yeah! Our hypothetical index is used. We also notice that the hypothetical
index creation is more or less 1ms, which is way less than the real index
creation would have last.

And of course, this hypothetical index is not used in an EXPLAIN ANALYZE:

{% highlight sql %}
rjuju=# EXPLAIN ANALYZE SELECT * FROM testable WHERE id < 1000 ;
                                                 QUERY PLAN
-------------------------------------------------------------------------------------------------------------
 Seq Scan on testable  (cost=0.00..17906.00 rows=916 width=15) (actual time=0.076..234.218 rows=999 loops=1)
   Filter: (id < 1000)
   Rows Removed by Filter: 999001
 Planning time: 0.083 ms
 Execution time: 234.377 ms
(5 rows)
{% endhighlight %}

Now let's go further:

{% highlight sql %}
rjuju=# EXPLAIN SELECT * FROM testable
rjuju=# WHERE id < 1000 and val LIKE 'line 100000%';
                                         QUERY PLAN
---------------------------------------------------------------------------------------------
 Index Scan using <41079>btree_testable_id on testable  (cost=0.30..30.62 rows=1 width=15)
   Index Cond: (id < 1000)
   Filter: (val ~~ 'line 100000%'::text)
(3 rows)
{% endhighlight %}

Our hypothetical index is still used, but an index on **id** and **val** should
help this query. Also, as there's a wildcard on the right-side of the LIKE
pattern, the operator class text\_pattern\_ops is needed. Let's check that:


{% highlight sql %}
rjuju=# SELECT hypopg_create_index('CREATE INDEX ON testable (id, val text_pattern_ops)');
 hypopg_create_index
---------------------
 t
(1 row)

Time: 1,194 ms

rjuju=# EXPLAIN SELECT * FROM testable
rjuju=# WHERE id < 1000 and val LIKE 'line 100000%';
                                              QUERY PLAN
------------------------------------------------------------------------------------------------------
 Index Only Scan using <41080>btree_testable_id_val on testable on testable  (cost=0.30..26.76 rows=1 width=15)
   Index Cond: ((id < 1000) AND (val ~>=~ 'line 100000'::text) AND (val ~<~ 'line 100001'::text))
   Filter: (val ~~ 'line 100000%'::text)
(3 rows)

{% endhighlight %}

And yes, PostgreSQL decides to use our new index!

### Index size estimation

For now, the index size estimation is done quickly, which can give us a clue on what
would be the real index size.

Let's check the estimated size of our two hypothetical indexes:

{% highlight sql %}
rjuju=# SELECT indexname,pg_size_pretty(hypopg_relation_size(indexrelid))
rjuju=# FROM hypopg();
           indexname           | pg_size_pretty 
-------------------------------+----------------
 <41080>btree_testable_id     | 25 MB
 <41079>btree_testable_id_val | 49 MB
(2 rows)

{% endhighlight %}

Now, create the real indexes, and compare the sizes:

{% highlight sql %}
rjuju=# CREATE INDEX ON testable (id);
CREATE INDEX
Time: 1756,001 ms

rjuju=# CREATE INDEX ON testable (id, val text_pattern_ops);
CREATE INDEX
Time: 2179,185 ms

rjuju=# SELECT relname,pg_size_pretty(pg_relation_size(oid))
rjuju=# FROM pg_class WHERE relkind = 'i' AND relname LIKE '%testable%';
       relname       | pg_size_pretty 
---------------------+----------------
 testable_id_idx     | 21 MB
 testable_id_val_idx | 30 MB
{% endhighlight %}

The estimated index size is a bit higher than the real size. It's on purpose.
If the estimated index size is less than an existing index, PostgreSQL would
prefer to use the hypothetical index than the real index, which is definitively
not interesting. Also, to simulate a bloated index (which is quite frequent on
real indexes), a hardcoded 20% bloat factor is added. Finally, the estimation could
also be improved a lot.

### Limitations

This 0.0.1 version of HypoPG is still a work in progress, and a lot of work
is still needed.

Here are the main limitations (at least that I'm aware of):

  * only btree hypothetical indexes are supported
  * no hypothetical indexes on expression
  * no hypothetical indexes on predicate
  * tablespace specification is not possible
  * index size estimation could be improved, and it's not possible to change
the bloat factor

However, I believe it can already be helpful.

### What's next ?

Now, the next step is to implement HypoPG support in
[PoWA](https://dalibo.github.io/powa/), to help DBA decide wether they should
create the suggested index or not, and remove the current limitations.

If you want to try HypoPG, here is the github repository:
[github.com/dalibo/hypopg](https://github.com/dalibo/hypopg).

Stay tuned!
