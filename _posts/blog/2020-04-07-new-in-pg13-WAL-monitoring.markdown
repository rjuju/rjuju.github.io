---
layout: post
title: "New in pg13: WAL monitoring"
modified:
categories: postgresql
excerpt:
tags: [postgresql, monitoring, pg13, new_feature]
lang: gb
image:
  feature:
date: 2020-04-07T17:46:15+02:00
---

Write-Ahead Logs is a critical part of PostgreSQL, that ensures data
durability.  While there are multiple [configuration parameters
](https://www.postgresql.org/docs/current/runtime-config-wal.html), there was
no easy to monitor WAL activity, or what is generating it.

### New infrastructure to track WAL activity

    commit df3b181499b40523bd6244a4e5eb554acb9020ce
    Author: Amit Kapila <akapila@postgresql.org>
    Date:   Sat Apr 4 10:02:08 2020 +0530

        Add infrastructure to track WAL usage.

        This allows gathering the WAL generation statistics for each statement
        execution.  The three statistics that we collect are the number of WAL
        records, the number of full page writes and the amount of WAL bytes
        generated.

        This helps the users who have write-intensive workload to see the impact
        of I/O due to WAL.  This further enables us to see approximately what
        percentage of overall WAL is due to full page writes.

        In the future, we can extend this functionality to allow us to compute the
        the exact amount of WAL data due to full page writes.

        This patch in itself is just an infrastructure to compute WAL usage data.
        The upcoming patches will expose this data via explain, auto_explain,
        pg_stat_statements and verbose (auto)vacuum output.

        Author: Kirill Bychik, Julien Rouhaud
        Reviewed-by: Dilip Kumar, Fujii Masao and Amit Kapila
        Discussion: https://postgr.es/m/CAB-hujrP8ZfUkvL5OYETipQwA=e3n7oqHFU=4ZLxWS_Cza3kQQ@mail.gmail.com

With this new infrastructure, each backend will track various information about
WAL generation: the number of WAL records, the size of WAL generated and the
number of full page images generated.  It also makes sure that parallel
queries, both DML and utility statements (for now only CREATE INDEX and VACUUM)
are correctly handled.

### Per-query WAL activity with pg_stat_statements

    commit 6b466bf5f2bea0c89fab54eef696bcfc7ecdafd7
    Author: Amit Kapila <akapila@postgresql.org>
    Date:   Sun Apr 5 07:34:04 2020 +0530

        Allow pg_stat_statements to track WAL usage statistics.

        This commit adds three new columns in pg_stat_statements output to
        display WAL usage statistics added by commit df3b181499.

        This commit doesn't bump the version of pg_stat_statements as the
        same is done for this release in commit 17e0328224.

        Author: Kirill Bychik and Julien Rouhaud
        Reviewed-by: Julien Rouhaud, Fujii Masao, Dilip Kumar and Amit Kapila
        Discussion: https://postgr.es/m/CAB-hujrP8ZfUkvL5OYETipQwA=e3n7oqHFU=4ZLxWS_Cza3kQQ@mail.gmail.com

This basically exposes the mentionned new information about WAL activity in
pg\_stat\_activity, so per (user, database, normalized query).  Here is an
example:

{% highlight sql %}
=# CREATE TABLE t1 (id integer);
CREATE

=# INSERT INTO t1 SELECT 1;
INSERT 0 1

=# UPDATE t1 SET id = 2 WHERE id = 1;
UPDATE 1

=# CHECKPOINT;
CHECKPOINT

=# DELETE FROM t1 WHERE id = 2;
DELETE 1
=# SELECT query, wal_records, wal_bytes, wal_num_fpw
   FROM pg_stat_statements
   WHERE query LIKE 'UPDATE%' OR query LIKE 'DELETE%';
                   query                | wal_records | wal_bytes | wal_num_fpw
-------------------------------------+-------------+-----------+-------------
 DELETE FROM t1 WHERE id = $1        |           1 |       155 |           1
 UPDATE t1 SET id = $1 WHERE id = $2 |           1 |        69 |           0
(2 rows)
{% endhighlight %}

I simply inserted a row, updated it and deleted it.  Now, looking specifically
at the UPDATE and the DELETE, the numbers can be surprising.

When inserting a row, we indeed expect a single WAL record and some WAL bytes
for the new row, with some overhead due to internal implementation.

Now, if you're familiar with PostgreSQL MVCC implementation, you should know
that doing a DELETE should only write a transaction id in the `xmax` field
([this documentation
page](https://www.postgresql.org/docs/current/storage-page-layout.html) is a
good introduction on that subject).  So why writing a 4B field (the size of the
recotded `xmax` field), even with some overhead, is writing more than twice the
amount of WAL that was required to update a full row?  That's because the
DELETE caused a [full page
write](https://www.postgresql.org/docs/current/runtime-config-wal.html#GUC-FULL-PAGE-WRITES).
This is a side effect of performing a **CHECKPOINT** before the DELETE.  To
guarantee data consistency (and if `full_page_writes` parameter isn't
deactivated), any block modified for the first time after a **CHECKPOINT**
completion will be fully logged, rather than logging only the delta.

You'll also note that the full page didn't generate 8kB of data as you could
expect.  This isn't because of `wal_compression`, as I didn't activate it, but
because the page is almost empty.  Indeed, as an optimization, any "hole" in
a page, as long as it's a standard page, can be safely skipped in the WAL.  If
you're curious, this is done in the [XLogRecordAssemble() function
](https://github.com/postgres/postgres/blob/master/src/backend/access/transam/xloginsert.c).
Here's the relevant extract:

{% highlight sql %}
static XLogRecData *
XLogRecordAssemble(RmgrId rmid, uint8 info,
				   XLogRecPtr RedoRecPtr, bool doPageWrites,
				   XLogRecPtr *fpw_lsn, int *num_fpw)
{
[...]
		/*
		 * If needs_backup is true or WAL checking is enabled for current
		 * resource manager, log a full-page write for the current block.
		 */
		include_image = needs_backup || (info & XLR_CHECK_CONSISTENCY) != 0;

		if (include_image)
		{
			Page		page = regbuf->page;
			uint16		compressed_len = 0;

			/*
			 * The page needs to be backed up, so calculate its hole length
			 * and offset.
			 */
			if (regbuf->flags & REGBUF_STANDARD)
			{
				/* Assume we can omit data between pd_lower and pd_upper */
				uint16		lower = ((PageHeader) page)->pd_lower;
				uint16		upper = ((PageHeader) page)->pd_upper;

				if (lower >= SizeOfPageHeaderData &&
					upper > lower &&
					upper <= BLCKSZ)
				{
					bimg.hole_offset = lower;
					cbimg.hole_length = upper - lower;
				}
				else
				{
					/* No "hole" to remove */
					bimg.hole_offset = 0;
					cbimg.hole_length = 0;
				}
			}
            [...]
{% endhighlight %}

### WAL activity in EXPLAIN (and auto_explain)

A new `WAL` option is available in the **EXPLAIN** command, and similarly a
`auto_explain.log_wal` for **auto_explain**, to display the same counters.  In
TEXT mode, only the non-zero counters are shown, similarly to other counters.
For instance:

{% highlight sql %}
=# EXPLAIN (ANALYZE, WAL, COSTS OFF) UPDATE t1 SET id = 1 WHERE id = 1;
                           QUERY PLAN
----------------------------------------------------------------
 Update on t1 (actual time=0.181..0.181 rows=0 loops=1)
   WAL:  records=1  bytes=68
   ->  Seq Scan on t1 (actual time=0.074..0.080 rows=1 loops=1)
         Filter: (id = 1)
 Planning Time: 0.274 ms
 Execution Time: 0.381 ms
(6 rows)
{% endhighlight %}

### WAL activity in autovacuum logs

And finally, if an autovacuum is logging its activity (when reaching the
`log_autovacuum_min_duration` threshold), the same information will be logged.
For instance, after inserting 100k records in the same table, deleting half of
them and running a **CHECKPOINT**, here's the output I get:

{% highlight sql %}
LOG:  automatic vacuum of table "rjuju.public.t1": index scans: 0
	pages: 0 removed, 443 remain, 0 skipped due to pins, 0 skipped frozen
	tuples: 50000 removed, 50001 remain, 0 are dead but not yet removable, oldest xmin: 496
	buffer usage: 912 hits, 3 misses, 448 dirtied
	avg read rate: 0.084 MB/s, avg write rate: 12.485 MB/s
	system usage: CPU: user: 0.17 s, system: 0.00 s, elapsed: 0.28 s
	WAL usage: 1330 records, 445 full page writes, 2197104 bytes
{% endhighlight %}

This new log output is in my opinion especially important, especially when it
comes to [anti-wraparound / FREEZE
vacuum](https://www.postgresql.org/docs/current/routine-vacuuming.html#VACUUM-FOR-WRAPAROUND).
Indeed, by nature an anti-wraparound VACUUM is more likely to touch blocks that
weren't modified for a long time as it's targeting tuple being visible for
more than 200M transactions (by default).  Even though it's only setting a flag
bit to mark the tuple as frozen, if that block wasn't modified since the last
**CHECKPOINT**, this bit will be amplified to a **full page image** which is
way more data.

With this new feature, it's now possible to really monitor the WAL
generation, which will help to better tune your instances!
