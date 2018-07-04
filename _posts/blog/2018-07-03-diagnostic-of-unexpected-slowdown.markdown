---
layout: post
title: "Diagnostic of an unexpected slowdown"
modified:
categories: postgresql
excerpt:
tags: [postgresql, performance]
image:
  feature:
date: 2018-07-03T19:56:34+02:00
---

This blog post is a summary of a production issue I had to investigate some
time ago with people from [Oslandia](https://oslandia.com/en/home-en/), and
since it's quite unusual I wanted to share it with some methodology I used, if
it can help anyone running into the same kind of problem.  It's also a good
opportunity to say that upgrading to a newer PostgreSQL version is almost
always a good idea.

### The problem

The initial performance issue reported enough information to know something
strange was happening.

The database is a PostgreSQL 9.3.5 (yes, missing some minor version updates),
and obviously a lot of major versions late.  The configuration also had quite
unusual settings.  The most relevant hardware and settings are:

    Server
        CPU: 40 core, 80 with hyperthreading enabled
        RAM: 128 GB
    PostgreSQL:
        shared_buffers: 16 GB
        max_connections: 1500

The high `shared_buffers`, especially given the quite old PostgreSQL version,
is a good candidate for more investigation.  The `max_connections` is also
quite high, but unfortunately the software vendor claims that using a
connection pooler isn't supported.  Therefore most of the connections are idle.
This isn't great because it implies quite some overhead to acquire a snapshot,
but there are enough CPU to handle quite a lot of connections.

The main problem was that sometimes, the same queries could be extremely
slower.  The following trivial example was provided:

{% highlight sql %}
EXPLAIN ANALYZE SELECT count(*) FROM pg_stat_activity ;

-- When the issue happens
"Aggregate  (actual time=670.719..670.720 rows=1 loops=1)"
"  ->  Nested Loop  (actual time=663.739..670.392 rows=1088 loops=1)"
"        ->  Hash Join  (actual time=2.987..4.278 rows=1088 loops=1)"
"              Hash Cond: (s.usesysid = u.oid)"
"              ->  Function Scan on pg_stat_get_activity s  (actual time=2.941..3.302 rows=1088 loops=1)"
"              ->  Hash  (actual time=0.022..0.022 rows=12 loops=1)"
"                    Buckets: 1024  Batches: 1  Memory Usage: 1kB"
"                    ->  Seq Scan on pg_authid u  (actual time=0.008..0.013 rows=12 loops=1)"
"        ->  Index Only Scan using pg_database_oid_index on pg_database d  (actual time=0.610..0.611 rows=1 loops=1088)"
"              Index Cond: (oid = s.datid)"
"              Heap Fetches: 0"
"Total runtime: 670.880 ms"

-- Normal timing
"Aggregate  (actual time=6.370..6.370 rows=1 loops=1)"
"  ->  Nested Loop  (actual time=3.581..6.159 rows=1088 loops=1)"
"        ->  Hash Join  (actual time=3.560..4.310 rows=1088 loops=1)"
"              Hash Cond: (s.usesysid = u.oid)"
"              ->  Function Scan on pg_stat_get_activity s  (actual time=3.507..3.694 rows=1088 loops=1)"
"              ->  Hash  (actual time=0.023..0.023 rows=12 loops=1)"
"                    Buckets: 1024  Batches: 1  Memory Usage: 1kB"
"                    ->  Seq Scan on pg_authid u  (actual time=0.009..0.014 rows=12 loops=1)"
"        ->  Index Only Scan using pg_database_oid_index on pg_database d  (actual time=0.001..0.001 rows=1 loops=1088)"
"              Index Cond: (oid = s.datid)"
"              Heap Fetches: 0"
"Total runtime: 6.503 ms"
{% endhighlight %}

So while the "good" timing is a little but slow (though there are 1500
connections), the "bad" timing is more than **100x slower**, for a very simple
query.

Another example of a trivial query on production data was provided, but with
some more informations.  Here's an anonymized version:

{% highlight sql %}
EXPLAIN (ANALYZE, BUFFERS) SELECT some_col
FROM some_table
WHERE some_indexed_col = 'value' AND uppser(other_col) = 'other_value'
LIMIT 1 ;

"Limit  (actual time=7620.756..7620.756 rows=0 loops=1)"
"  Buffers: shared hit=43554"
"  ->  Index Scan using idx_some_table_some_col on some_table  (actual time=7620.754..7620.754 rows=0 loops=1)"
"        Index Cond: ((some_indexed_cold)::text = 'value'::text)"
"        Filter: (upper((other_col)::text) = 'other_value'::text)"
"        Rows Removed by Filter: 17534"
"        Buffers: shared hit=43554"
"Total runtime: 7620.829 ms"

"Limit  (actual time=899.607..899.607 rows=0 loops=1)"
"  Buffers: shared hit=43555"
"  ->  Index Scan using idx_some_table_some_col on some_table  (actual time=899.605..899.605 rows=0 loops=1)"
"        Index Cond: ((some_indexed_cold)::text = 'value'::text)"
"        Filter: (upper((other_col)::text) = 'other_value'::text)"
"        Rows Removed by Filter: 17534"
"        Buffers: shared hit=43555"
"Total runtime: 899.652 ms"
{% endhighlight %}

There was also quite some instrumentation data on O/S side, showing that
neither the disk, CPU or RAM where exhausted, and no interesting message in
`dmesg` or any system log.

### What do we know?

For the first query, we see that the inner index scan average time raises from
**0.001ms** to **0.6ms**:

{% highlight none %}
->  Index Only Scan using idx on pg_database (actual time=0.001..0.001 rows=1 loops=1088)

->  Index Only Scan using idx on pg_database (actual time=0.610..0.611 rows=1 loops=1088)
{% endhighlight %}

With a high `shared_buffers` setting and an old PostgreSQL version, a common
issue is a slowdown when the dataset is larger that the `shared_buffers`, due
to the **clocksweep** algorithm used to evict buffers.

However, the second query shows that the same thing is happening while all the
blocks are in `shared_buffers`.  This cannot be a buffer eviction problem due
to too high `shared_buffers` setting, or any disk latency issue.

While some PostgreSQL configuration settings could be changed, none of them
seems to explain this exact behavior.  It'd be likely that modifying them will
fix the situation, but we need more information to know exactly what's
happening here and avoid any further performance issue.

### Any wild guess?

Since the simple explanations have been discarded, it's necessary to think
about lower level explanations.

If you followed the latest PostgreSQL versions enhancements, you should have
noticed quite a few optimizations on scalability and locking.  If you want more
information, there are plenty of blog entries about these, for instance [this
great
article](http://amitkapila16.blogspot.tw/2015/01/read-scalability-in-postgresql-95.html).

On the kernel side and given the high number of connections, it also can be,
and it's probably the most likely explanation, a
[TLB](https://en.wikipedia.org/wiki/Translation_lookaside_buffer) exhaustion.

In any case, in order to confirm any theory we need to use very specific tools.

### Deeper analysis: TLB exhaustion

Without going to deep, you need to know that each processus has an area of
kernel memory used to store the [page tables
entries](https://en.wikipedia.org/wiki/Page_table#PTE), called the `PTE`.  This
area is usually not big.  But since PostgreSQL is relying on multiple processes
accessing a big chunk of shared memory, each process will have an entry for
each 4kB (the default page size) address of the shared buffers it has accessed.
So you can end up with quite a lot of memory used for the `PTE`.

You can know the size of the `PTE` at the O/S level looking for the **VmPTE**
entry in the processus status.  You can also check the **RssShmem** entry to
know how many shared memory pages is mapped.  For instance:

{% highlight bash %}
egrep "(VmPTE|RssShmem)" /proc/${PID}/status
RssShmem:	     340 kB
VmPTE:	     140 kB
{% endhighlight %}

This process didn't access lots of buffers, so the PTE is small.  If we try
with a process which has accessed all the buffers of a 8 GB shared\_buffers:

{% highlight bash %}
egrep "(VmPTE|RssShmem)" /proc/${PID}/status
RssShmem:	 8561116 kB
VmPTE:	   16880 kB
{% endhighlight %}

It's **16 MB** used for the PTE!  Multiplying that with the number of
connections, and you end up with gigabytes of memory used for the PTE.
Obviously, this wont' fit in the TLB.  As a consequence, the processes will
have a lot of TLB miss every time they need to access a page in memory,
drastically increasing the latency.

On the system that had performance issue, with **16 GB** of shared buffers and
**1500** long lived connections, the total memory size of the combined PTE was
around 45 GB.  A rough approximation can be done with this small script:

{% highlight bash %}
for p in $(pgrep postgres); do grep "VmPTE:" /proc/$p/status; done | awk '{pte += $2} END {print pte / 1024 / 1024}'
{% endhighlight %}

**NOTE:** This will compute the memory used for the PTE of all postgres
processes.  If you have multiple clusters on the same machine and you want to
have per cluster information, you need to adapt this command to only match the
processes whose ppid are you cluster's postmaster pid.
{: .notice}

This is evidently the culprit here.  Just to be sure, let's look at what `perf`
show when the performance slowdown occurs, and when it doesn't.

Here are the top consuming functions (consuming more than 2% of CPU time)
reported by perf when everything is fine:

{% highlight none %}
# Children      Self  Command          Symbol
# ........  ........  ...............  ..................
     4.26%     4.10%  init             [k] intel_idle
     4.22%     2.22%  postgres         [.] SearchCatCache
{% endhighlight %}

Nothing really interesting, the system was really not saturated.  Now when
the problem occurs:

{% highlight none %}
# Children      Self  Command          Symbol
# ........  ........  ...............  ....................
     8.96%     8.64%  postgres         [.] s_lock
     4.50%     4.44%  cat              [k] smaps_pte_entry
     2.51%     2.51%  init             [k] poll_idle
     2.34%     2.28%  postgres         [k] compaction_alloc
     2.03%     2.03%  postgres         [k] _spin_lock
{% endhighlight %}

We can see `s_lock`, the postgres function that wait on a spinlock consuming
almost 9% of the CPU time.  Then with almost 5% of the CPU time is consumed by
`smaps_pte_entry`, a kernel function doing the translation for a single entry.
This is supposed to be extremely fast, and shouldn't even appear in a perf
record!  This certainly explain the extreme slowdown, and the lack of high
lever counters able to explain such slowdowns.

### The solution

Multiple solutions are possible to solve this problem.

The usual answer is to [ask PostgreSQL to allocate the `shared_buffers` in huge
pages](https://www.postgresql.org/docs/current/static/kernel-resources.html#LINUX-HUGE-PAGES).
Indeed, with 2MB pages instead of 4kB, the memory needed for PTE will
automatically drop 512 times.  This would be an easy and huge win.
Unfortunately, this is only possible since version 9.4, but upgrading wasn't
even an option, since the software vendor claimed that only the 9.3 version is
supported.

Another way to reduce the PTE size is to reduce the number of connections,
which was quite high.  Unfortunately again, the vendor claimed that connection
poolers aren't supported, and the customer needed that many connections.

So the only remaining solution was therefore to reduce the shared\_buffers.
After some tries, the higher value that could be used to avoid the extreme
slowdown was **4 GB**.  Fortunately, PostgreSQL was able to have quite good
performance with this size of cache.

If software vendors read this post, please understand that if people ask for
newer PostgreSQL version compatibility, or pooler compatibility, they have very
good reasons for that.  There are usually very few behavior changes with newer
versions, and they're all documented!
