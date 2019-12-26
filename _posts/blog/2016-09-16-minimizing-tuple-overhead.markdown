---
layout: post
title: "Minimizing tuple overhead"
modified:
categories: postgresql
excerpt:
tags: [postgresql, performance, PoWA]
lang: gb
image:
  feature:
date: 2016-09-16T14:03:34+02:00
---

I hear quite often people being disappointed on how much space PostgreSQL is
wasting for each row it stores.  I'll try to show here some tricks to minimize
this effect, to allow more efficient storage.

### What overhead?

If you don't have tables with more than few hundred of million of rows, it's
likely that you didn't have an issue with this.

For each row stored, postgres will store aditionnal data for its own need.
This is
[documented here](https://www.postgresql.org/docs/current/static/storage-page-layout.html#HEAPTUPLEHEADERDATA-TABLE).
The documentation says:

| Field       | Type            | Length  | Description                                           |
|-------------|-----------------|---------|-------------------------------------------------------|
| t_xmin      | TransactionId   | 4 bytes | insert XID stamp                                      |
| t_xmax      | TransactionId   | 4 bytes | delete XID stamp                                      |
| t_cid       | CommandId       | 4 bytes | insert and/or delete CID stamp (overlays with t_xvac) |
| t_xvac      | TransactionId   | 4 bytes | XID for VACUUM operation moving a row version         |
| t_ctid      | ItemPointerData | 6 bytes | current TID of this or newer row version              |
| t_infomask2 | uint16          | 2 bytes | number of attributes, plus various flag bits          |
| t_infomask  | uint16          | 2 bytes | various flag bits                                     |
| t_hoff      | uint8           | 1 byte  | offset to user data                                   |

Which is **23 bytes** on most architectures (you have either **t_cid** or
**t_xvac**).

You can see part of these fields in hidden column present on any table by
adding them in the SELECT part of a query, or look for negative attribute
number in **pg_attribute** catalog:

{% highlight sql %}
# \d test
     Table "public.test"
 Column |  Type   | Modifiers
--------+---------+-----------
 id     | integer |

# SELECT xmin, xmax, id FROM test LIMIT 1;
 xmin | xmax | id
------+------+----
 1361 |    0 |  1

# SELECT attname, attnum, atttypid::regtype, attlen
FROM pg_class c
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE relname = 'test'
ORDER BY attnum;
 attname  | attnum | atttypid | attlen
----------+--------+----------+--------
 tableoid |     -7 | oid      |      4
 cmax     |     -6 | cid      |      4
 xmax     |     -5 | xid      |      4
 cmin     |     -4 | cid      |      4
 xmin     |     -3 | xid      |      4
 ctid     |     -1 | tid      |      6
 id       |      1 | integer  |      4
{% endhighlight %}

If you compare to the previous table, you can see than not all of these columns
are not stored on disk.  Obviously PostgreSQL doesn't store the table's oid in
each row.  It's added after, while constructing a tuple.

If you want more technical details, you should read take a look at
[htup_detail.c](http://doxygen.postgresql.org/htup__details_8h.html), starting
with
[TupleHeaderData struct](http://doxygen.postgresql.org/structHeapTupleHeaderData.html).

### How costly is it?

As the overhead is fixed, it'll become more and more neglictable as the row
size grows.  If you only store a single int column (**4 bytes**), each row will
need:

{% highlight C %}
23B + 4B = 27B
{% endhighlight %}

So, it's **85% overhead**, pretty horrible.

On the other hand, if you store 5 integer, 3 bigint and 2 text columns (let's
say ~80B average), you'll have:

{% highlight C %}
23B + 5*4B + 3*8B + 2*80B = 227B
{% endhighlight %}

That's "only" **10% overhead**.

### So, how to minimize this overhead

The idea is to store the same data with less records.  How to do that?
Aggregating data in arrays.  The more records you put in a single array, the
less overhead you have.  And if you aggregate enough data, you can benefit
from transparent compression thanks to the [TOAST
mechanism](https://www.postgresql.org/docs/current/static/storage-toast.html)

Let's try with a single 1 integer column table containing 10M rows:

{% highlight sql %}
# CREATE TABLE raw_1 (id integer);

# INSERT INTO raw_1 SELECT generate_series(1,10000000);

# CREATE INDEX ON raw_1 (id);
{% endhighlight %}

The user data should need 10M * 4B, ie. around **38MB**, while this table will
consume **348MB**.  Inserting the data takes around **23** seconds.

**NOTE:** If you do the maths, you'll find out that the overhead is slighty
more than **32B**, not **23B**.  This is because each block also has some
overhead, NULL handling and alignement issue.  If you want more information
on this, I recommand to see
[this presentation](https://github.com/dhyannataraj/tuple-internals-presentation)
{: .notice}

Let's compare with aggregated versions of the same data:

{% highlight sql %}
# CREATE TABLE agg_1 (id integer[]);

# INSERT INTO agg_1 SELECT array_agg(i)
FROM generate_series(1,10000000) i
GROUP BY i % 2000000;

# CREATE INDEX ON agg_1 (id);
{% endhighlight %}

This will insert 5 elements per row.  I've done the same test with 20, 100, 200
and 1000 elements per row.  Results are below:

[![Benchmark 1](/images/tuple_overhead_1.svg)](/images/tuple_overhead_1.svg)


**NOTE:** The size for 1000 element per row is a little higher than lower value.
This is because it's the only one which is big enough to be TOAST-ed, but not
big enough to be compressed.  We can see a little TOAST overhead here.
{: .notice}

So far so good, we can see quite good improvements, both in size and INSERT
time even for very small arrays.  Let's see the impact to retrieve rows.  I'll
try to retrieve all the rows, then only one row with an index scan (for the
tests I've used EXPLAIN ANALYZE to minimize the time to represent the data in
psql):

{% highlight sql %}
# SELECT id FROM raw_1;

# CREATE INDEX ON raw_1 (id);

# SELECT * FROM raw_1 WHERE id = 500;
{% endhighlight %}

To properly index this array, we need a GIN index.  To get all the values from
aggregated data, we need to unnest() the arrays, and to be a little more
creative to get a single record:

{% highlight sql %}
# SELECT unnest(id) AS id FROM agg_1;

# CREATE INDEX ON agg_1 USING gin (id);

# WITH s(id) AS (
    SELECT unnest(id)
    FROM agg_1
    WHERE id && array[500]
)
SELECT id FROM s WHERE id = 500;
{% endhighlight %}

Here's the chart comparing index creation time and index size:

[![Benchmark 2](/images/tuple_overhead_2.svg)](/images/tuple_overhead_2.svg)

The GIN index is a little more than twice the btree index, if I add the table
size, total size is almost the same as without aggregation.  That's not a big
issue since this example is naive, we'll see later how to avoid using GIN
index to keep total size low.  Also index is way slower to build, meaning that
INSERT will also be slower.

Here's the chart comparing the time to retrieve all rows and a single row:

[![Benchmark 3](/images/tuple_overhead_3.svg)](/images/tuple_overhead_3.svg)

Getting all the rows is probably not an interesting example, but it's
interesting to note that as soon as array contains enough elements it starts to
be faster than the same operation using the original table.  We also see that
getting only one element is much more faster than with the btree index, thanks
to GIN efficiency.  It's not tested here, but since only btree index are
sorted, if you need to get a lot of data sorted, using a GIN index will require
an extra sort which will be way slower than a simple btree index scan.

### A more realistic example

Now that we've seen the basics, let's see how to go further: aggregating more
than one columns and avoid to use too much disk space (and slowdown at write
time) with a GIN index.  For this, I'll present how
[PoWA](https://powa.readthedocs.io/) stores it's data.

For each datasource collected, two tables are used: one for the *historic and
aggregated* data, and one the *current data*.  These tables store data in a
custom type instead of plain columns. Let's see the tables related to
**pg_stat_statements**:

The custom type, basically all the counters present in pg_stat_statements and
the timestamp associated to this record:

{% highlight sql %}
powa=# \d powa_statements_history_record
   Composite type "public.powa_statements_history_record"
       Column        |           Type           | Modifiers
---------------------+--------------------------+-----------
 ts                  | timestamp with time zone |
 calls               | bigint                   |
 total_time          | double precision         |
 rows                | bigint                   |
 shared_blks_hit     | bigint                   |
 shared_blks_read    | bigint                   |
 shared_blks_dirtied | bigint                   |
 shared_blks_written | bigint                   |
 local_blks_hit      | bigint                   |
 local_blks_read     | bigint                   |
 local_blks_dirtied  | bigint                   |
 local_blks_written  | bigint                   |
 temp_blks_read      | bigint                   |
 temp_blks_written   | bigint                   |
 blk_read_time       | double precision         |
 blk_write_time      | double precision         |
{% endhighlight %}

The table for current data stores the pg_stat_statement unique identifier (queryid,
dbid, userid), and a record of counters:

{% highlight sql %}
powa=# \d powa_statements_history_current
    Table "public.powa_statements_history_current"
 Column  |              Type              | Modifiers
---------+--------------------------------+-----------
 queryid | bigint                         | not null
 dbid    | oid                            | not null
 userid  | oid                            | not null
 record  | powa_statements_history_record | not null
{% endhighlight %}

The table for aggregated data contains the same unique identifier, an array of
records and some special fields:

{% highlight sql %}
powa=# \d powa_statements_history
            Table "public.powa_statements_history"
     Column     |               Type               | Modifiers
----------------+----------------------------------+-----------
 queryid        | bigint                           | not null
 dbid           | oid                              | not null
 userid         | oid                              | not null
 coalesce_range | tstzrange                        | not null
 records        | powa_statements_history_record[] | not null
 mins_in_range  | powa_statements_history_record   | not null
 maxs_in_range  | powa_statements_history_record   | not null
Indexes:
    "powa_statements_history_query_ts" gist (queryid, coalesce_range)
{% endhighlight %}

We also store the timestamp range (*coalesce_range*) containing all aggregated
counters in the row, and the minimum and maximum values of each counter in two
dedicated records.  These extra fields doesn't consume too much space, and
allows very efficient indexing and computation, based on the data access
pattern of the related application.

This table is used to know how much ressource a query consumed on a given time
range.  The GiST index won't be too big since it only indexes two small values
per X aggregated counters, and will find efficiently the rows matching a given
queryid and time range.

Then, computing the resources consumed can be done efficiently, since the
pg_stat_statements counters are strictly monotonic.  The algorithm would be:

* if the row time range is entirely contained in the asked time range, we only
  need to compute delta of summary record:
  **maxs_in_range.counter - mins_in_range.counter**
* if not (meaning only two rows for each queryid) we unnest the array, filter
  out records that aren't in the asked time range, keep first and last value
  and compute for each counter the maximum minus the minimum.


**NOTE:** Actually, PoWA interface always unnest all records contained in the
asked time interval, since the interface is designed to show these counters
evolution on a relatively small time range, but with a great precision.
Hopefuly, unnesting the arrays is not that expensive, especially compared to
the disk space saved.
{: .notice}

And here's the size needed for the aggregated and non aggregated values.  For
this I let PoWA generate **12.331.366 records** (configuring a snapshot every 5
seconds for some hours, with default aggregation of 100 records per row), and
used a btree index on (queryid, ((record).ts) to simulate the index present on
the aggregated table:

[![Benchmark 4](/images/tuple_overhead_4.svg)](/images/tuple_overhead_4.svg)

Pretty efficient, right?

### Limitations

There are some limitations with aggregating records.  If you do this, you can't
enforce constraints such as foreign keys or unique constraints.  The use is
therefore non-relationnal data, such as counters or metadata.

### Bonus

Using custom types also allows some nice things, like defining **custom
operators**.  For instance, the release 3.1.0 of PoWA provides two operators
for each custom type defined:

* the **-** operator, to get difference between two record
* the **/** operator, to get the difference *per second*

You can therefore do quite easily this kind of queries:

{% highlight sql %}
# SELECT (record - lag(record) over()).*
FROM from powa_statements_history_current
WHERE queryid = 3589441560 AND dbid = 16384;
      intvl      | calls  |    total_time    |  rows  | ...
-----------------+--------+------------------+--------+ ...
 <NULL>          | <NULL> |           <NULL> | <NULL> | ...
 00:00:05.004611 |   5753 | 20.5570000000005 |   5753 | ...
 00:00:05.004569 |   1879 | 6.40500000000047 |   1879 | ...
 00:00:05.00477  |  14369 | 48.9060000000006 |  14369 | ...
 00:00:05.00418  |      0 |                0 |      0 | ...

# SELECT (record / lag(record) over()).*
FROM powa_statements_history_current
WHERE queryid = 3589441560 AND dbid = 16384;

  sec   | calls_per_sec | runtime_per_sec  | rows_per_sec | ...
--------+---------------+------------------+--------------+ ...
 <NULL> |        <NULL> |           <NULL> |       <NULL> | ...
      5 |        1150.6 |  4.1114000000001 |       1150.6 | ...
      5 |         375.8 | 1.28100000000009 |        375.8 | ...
      5 |        2873.8 | 9.78120000000011 |       2873.8 | ...

{% endhighlight %}

If you're interested on how to implement such operators, you can look at
[PoWA implementation](https://github.com/powa-team/powa-archivist/commit/203ed02a5205ad41ce0854bf0580779d7fb6193b#diff-efeed95efc180d43a149361145c2f082R1079).

### Conclusion

You now know the basics to work around the per tuple overhead.  Depending on
your needs and your data specifities, you should find a way to aggregate your
data, maybe add some extra columns, to keep nice performance and spare some
disk space.

<!--
Test 1, simple integer, 10M row

with s(id) AS (select unnest(id) from agg_1 where id && array[500])
select * from s where id = 500;


raw_1 (id integer)
  insert: 23s
  size: 346 MB
  read data: 2.2s
  create index: 5.2s
  index size: 214 MB
  find 1 row: 1.4ms

agg_1 (id integer[])
  5 val per row
  INSERT INTO agg_1 SELECT array_agg(i) FROM generate_series(1,10000000) i GROUP BY i % 2000000 ;
  insert: 18s
  size: 146 MB (no toast)
  read raw data: 377 ms
  unnnest: 4s
  create (GIN) index: 73s
  index size: 478 MB
  find 1 val: 0.25ms

agg_1 (id integer[])
  20 val per row
  INSERT INTO agg_1 SELECT array_agg(i) FROM generate_series(1,10000000) i GROUP BY i % 500000 ;
  insert: 13s
  size: 64 MB (no toast)
  read raw data: 100ms
  read unnnest: 2.6 s
  create (GIN) index: 70s
  index size: 478MB
  find 1 val: 0.3ms

agg_1 (id integer[])
  100 val per row
  INSERT INTO agg_1 SELECT array_agg(i) FROM generate_series(1,10000000) i GROUP BY i % 100000;
  insert: 10s
  size: 43MB (notoast)
  read raw data: 31ms
  read unnnest: 2s
  create (GIN) index: 68s
  index size: 478 MB
  find 1 val: 0.45 ms

agg_1 (id integer[])
  200 val per row
  INSERT INTO agg_1 SELECT array_agg(i) FROM generate_series(1,10000000) i GROUP BY i % 50000;
  insert: 9.7s
  size: 43MB (notoast)
  read raw data: 21ms
  read unnnest: 2s
  create (GIN) index: 69s
  index size: 478MB
  find 1 val: 0.7ms

agg_1 (id integer[])
  1000 val per row
  INSERT INTO agg_1 SELECT array_agg(i) FROM generate_series(1,10000000) i GROUP BY i % 10000;
  insert: 10s
  size: 53MB (toast)
  read raw data: 7ms
  read unnnest: 2s
  create (GIN) index: 67s
  index size: 478MB
  find 1 val: 2,7ms
  -->
