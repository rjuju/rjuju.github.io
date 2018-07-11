---
layout: post
title: "Estimating Needed Memory for a Sort"
modified:
categories: postgresql
excerpt:
tags: [tuning, postgresql, performance]
lang: gb
image:
  feature:
date: 2015-08-18T16:03:34+02:00
---

### work\_mem?

The work memory, or **work\_mem** is one of the hardest thing to configure. It
can be used for various purposes. It's mainly used when sorting data or creating
hash tables, but it can also be used by set returning functions using a
tuplestore for instance, like the **generate\_series()** function. And each node
of a query can use this amount of memory. Set this parameter too low, and a lot
of temporary files will be used, set it too high and you may encounter errors,
or even an Out Of Memory (OOM) depending on your OS configuration.

I'll focus here on the amount of memory needed when sorting data, to help you
understand how much memory is required when PostgreSQL runs a sort operation.

### Truth is out

I often hear people say there is a correlation between the size of the temporary
files generated and the amount of data needed. It's wrong, you can't make any
assumption on the value of work\_mem based on the size of a sort temporary file.

It's because when the data to be sorted don't fit in the allowed memory,
PostgreSQL switches to an external sort. In addition to the currently used
memory, a temporary file is used multiple times, to avoid wasting disk space. If
you want more details on this, the relevant source code is present in
[tuplesort.c](https://github.com/postgres/postgres/blob/master/src/backend/utils/sort/tuplesort.c)
and
[logtapes.c](https://github.com/postgres/postgres/blob/master/src/backend/utils/sort/logtape.c).
As a brief introduction, the header of **tuplesort.c** says:

> [...]
> This module handles sorting of heap tuples, index tuples, or single
> Datums (and could easily support other kinds of sortable objects,
> if necessary).  It works efficiently for both small and large amounts
> of data.  Small amounts are sorted in-memory using qsort().  Large
> amounts are sorted using temporary files and a standard external sort
> algorithm.
>
> See Knuth, volume 3, for more than you want to know about the external
> sorting algorithm.  We divide the input into sorted runs using replacement
> selection, in the form of a priority tree implemented as a heap
> (essentially his Algorithm 5.2.3H), then merge the runs using polyphase
> merge, Knuth's Algorithm 5.4.2D.  The logical "tapes" used by Algorithm D
> are implemented by logtape.c, which avoids space wastage by recycling
> disk space as soon as each block is read from its "tape".
> [...]

It can be easily verified. First, let's create a table and add some data:

{% highlight sql %}
rjuju=# CREATE TABLE sort(id integer, val text);
CREATE TABLE
rjuju=# INSERT INTO sort SELECT i, 'line ' || i
FROM generate_series(1,100000) i;

INSERT 0 100000
{% endhighlight %}

To sort all these row, `7813kB` is needed (more details later). Let's see the
EXPLAIN ANALYZE with work\_mem set to `7813kB` and `7812kB`:

{% highlight sql %}
rjuju=# SET work_mem to '7813kB';
SET
rjuju=# EXPLAIN ANALYZE SELECT * FROM sort ORDER BY id;
                                                    QUERY PLAN
-------------------------------------------------------------------------------------------------------------------
 Sort  (cost=9845.82..10095.82 rows=100000 width=14) (actual time=50.957..59.163 rows=100000 loops=1)
   Sort Key: id
   Sort Method: quicksort  Memory: 7813kB
   ->  Seq Scan on sort  (cost=0.00..1541.00 rows=100000 width=14) (actual time=0.012..19.789 rows=100000 loops=1)

rjuju=# SET work_mem to '7812kB';
SET

rjuju=# EXPLAIN ANALYZE SELECT * FROM sort ORDER BY id;
                                                    QUERY PLAN
-------------------------------------------------------------------------------------------------------------------
 Sort  (cost=9845.82..10095.82 rows=100000 width=14) (actual time=142.662..168.596 rows=100000 loops=1)
   Sort Key: id
   Sort Method: external sort  Disk: 2432kB
   ->  Seq Scan on sort  (cost=0.00..1541.00 rows=100000 width=14) (actual time=0.027..18.621 rows=100000 loops=1)
{% endhighlight %}

So, `7813kB` are needed, and if we lack only `1kB`, the temporary file size
is `2432kB`.

You can also activate the trace\_sort parameter to have some more information:

{% highlight sql %}
rjuju=# SET trace_sort TO on;
SET
rjuju=# SET client_min_messages TO log;
SET

rjuju=# EXPLAIN ANALYZE SELECT * FROM sort ORDER BY id;
LOG:  begin tuple sort: nkeys = 1, workMem = 7812, randomAccess = f
LOG:  switching to external sort with 28 tapes: CPU 0.00s/0.05u sec elapsed 0.05 sec
LOG:  performsort starting: CPU 0.00s/0.07u sec elapsed 0.07 sec
LOG:  finished writing final run 1 to tape 0: CPU 0.00s/0.15u sec elapsed 0.15 sec
LOG:  performsort done: CPU 0.00s/0.15u sec elapsed 0.15 sec
LOG:  external sort ended, 304 disk blocks used: CPU 0.00s/0.18u sec elapsed 0.19 sec
                                                    QUERY PLAN
-------------------------------------------------------------------------------------------------------------------
 Sort  (cost=9845.82..10095.82 rows=100000 width=14) (actual time=154.751..181.724 rows=100000 loops=1)
   Sort Key: id
   Sort Method: external sort  Disk: 2432kB
   ->  Seq Scan on sort  (cost=0.00..1541.00 rows=100000 width=14) (actual time=0.039..23.712 rows=100000 loops=1)
{% endhighlight %}

With these data, 28 tapes are used.

### So, how do I know how much work\_mem is needed?

First, you need to know that all the data will be allocated through PostgreSQL's
allocator **AllocSet**. If you want to know more about it, I recommend to read
the excellent articles Tomas Vondras wrote on this topic: [Introduction to
memory
contexts](http://blog.pgaddict.com/posts/introduction-to-memory-contexts),
[Allocation set
internals](http://blog.pgaddict.com/posts/allocation-set-internals) and [palloc
overhead examples](http://blog.pgaddict.com/posts/palloc-overhead-examples).

The needed information here is that the allocator adds some overhead. Each
allocated block has a fixed overhead of `16B`, and the memory size requested
(without the 16B overhead) will be rounded up to a `2^N` size. So if you ask
for 33B, 80B will be used: 16B of overhead and 64B, the closest 2^N multiple.
The work\_mem will be used to store every row, and some more information.

For each row to sort, a fixed amount of `24B` memory will be used. This is the
size of a **SortTuple** which is the structure sorted. This amount of memory
will be allocated in a single block, so we have only `24B` overhead (fixed 8B
and the 16B to go to the closest 2^N multiple).

The first part of the formula is therefore:

{% highlight sql %}
24 * n + 24
{% endhighlight %}

(n being the number of tuple sorted)

Then, you have to know that PostgreSQL will preallocate this space for 1024
rows. So you'll never see a memory consumption of 2 or 3kB.

Then, each SortTuple will then contain a
**MinimalTuple**, which is basically a tuple without the system metadata (xmin,
xmax...), or an **IndexTuple** if the tuples come from an index scan. This
structure will be allocated separately for each tuple, so there can be a pretty
big overhead. Theses structures lengths are both `6B`, but need to be aligned.
This represents `16B` per tuple.

These structures will also contain the entire row, the size depends on the
table, and the content for variable length columns.

The second part of the formula is therefore:

{% highlight sql %}
(8 + ( (16 + average row length) rounded to 2^N) ) * n
{% endhighlight %}

We can now estimate how much memory is needed:

{% highlight sql %}
(24 + 8 + ( (16 + average row length) rounded to 2^N) ) * n + 24
{% endhighlight %}

### Testing the formula

Let's see on our table. It contains two fields, **id** and **val**. **id** is an
integer, so it uses `4B`. The **val** column is variable length. First, figure
out the estimated average row size:

{% highlight sql %}
rjuju=# SELECT stawidth
FROM pg_statistic WHERE starelid = 'sort'::regclass AND staattnum = 2;
 stawidth
----------
       10
{% endhighlight %}

Just to be sure, as I didn't do any ANALYZE on the table:

{% highlight sql %}
rjuju=# SELECT avg(length(val)) FROM sort;
        avg
--------------------
 9.8889500000000000
{% endhighlight %}

So, the average row size is approximatively `14B`. PostgreSQL showed the same
estimation on the previous EXPLAIN plan, the reported width was 14:

{% highlight sql %}
Sort  (cost=9845.82..10095.82 rows=100000 width=14) [...]
{% endhighlight %}

**NOTE:** It's better to rely on the pg\_statistic, because it's faster and
doesn't consume resources.  Also, if you have large fields, they'll be toasted,
and only a pointer will be stored in work\_mem, not the entire field
{: .notice}

We add the `16B` overhead for the **MinimalTuple** structure and get `30B`. This will lead to an allocated space of `32B`.

Finally, the table contains 100.000 tuples, we can now compute the memory
needed :

{% highlight sql %}
    (24 + 16 + 8 + 32) * 100000 + 24 = 8000024B = 7812,52kB
{% endhighlight %}

We now find the `7813kB` I announced earlier!

This is a very simple example. If you only sort some of the rows, the estimated
size can be too high or too low if the rows you sort don't match the average
size.

Also, note that if the data length of a row exceed `8kB` (not counting the
toasted data), the allocated size won't be rounded up to the next 2^N multiple.

### Wait, what about NULLs?

Yes, this formula was way too simple...

The formula assume you don't have any NULL field, so it compute the **maximum
estimated** memory needed.

A NULL field won't consume space for data, obviously, but will add a bit in a
bitmap stored in the MinimalTuple.

If at least one field of a tuple is NULL, the bitmap will be created. Its size
is:

{% highlight sql %}
(number of attribute + 7) / 8) bytes (rounded down)
{% endhighlight %}

So, if a tuple has 3 integer fields, and two of them are NULL, the data size will not be `16B` but:

{% highlight sql %}
4 + ( (3+7) / 8) = 5B
{% endhighlight %}

You can then try to estimate a better size with the statistic NULL fractions of
each attribute, available in **pg_statistics**.

### For the lazy ones

Here's a simple query that will do the maths for you. It assumes:

  * only fields from one table is sorted
  * there are no NULL
  * all the rows will be sorted
  * statistics are accurate

{% highlight sql %}
WITH RECURSIVE overhead(n) AS (
    SELECT 1
    UNION ALL
    SELECT n*2 FROM overhead
    WHERE n <= 4096
),
width AS (
    SELECT starelid,sum(stawidth) AS sum
    FROM pg_statistic
    GROUP BY 1
),
num_of_lines AS (
    SELECT relid,n_live_tup as n
    FROM pg_stat_user_tables

)

SELECT pg_size_pretty(((24 + 16 + 8 + max(o.n)*2) * (min(nol.n))) + 24)
FROM overhead o
CROSS JOIN pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN width w ON w.starelid = c.oid
JOIN num_of_lines nol ON nol.relid = c.oid
WHERE
c.relname = 'sort'
AND n.nspname = 'public'
AND o.n < (w.sum + 16);
 pg_size_pretty
----------------
 7813 kB
{% endhighlight %}

### Conclusion

Now, you know the basics to estimate the amount of memory you need to sort your
data.

A minimal example was presented here for a better understanding, things start to
get really complicated when you don't only sort all the rows of a single table
but the result of some joins and filters.

I hope you'll have fun tuning work\_mem on your favorite cluster. But don't
forget, work\_mem is used for more than just sorting tuples!

