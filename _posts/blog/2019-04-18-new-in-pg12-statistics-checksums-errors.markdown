---
layout: post
title: "New in pg12: Statistics on checkums errors"
modified:
categories: postgresql
excerpt:
tags: [postgresql, monitoring, pg12, new_feature]
lang: gb
image:
  feature:
date: 2019-04-18T13:02:26+02:00
---

### Data checksums

Added in [PostgreSQL
9.3](https://git.postgresql.org/gitweb/?p=postgresql.git;a=commitdiff;h=96ef3b8ff1c),
[data
checksums](https://www.postgresql.org/docs/current/app-initdb.html#APP-INITDB-DATA-CHECKSUMS)
can help to detect data corruption happening on the storage side.

Checksums are only enabled if the instance was setup using `initdb
--data-checksums` (which isn't the default behavior), or if activated
afterwards with the new
[pg_checksums](https://www.postgresql.org/docs/devel/app-pgchecksums.html)
tool also [added in PostgreSQL
12](https://git.postgresql.org/gitweb/?p=postgresql.git;a=commitdiff;h=ed308d783790).

When enabled, checksums are written each time a block is written to disk, and
verified each time a block is read from disk (or from the operating system
cache).  If the checksum verification fails, an error is reported in the logs.
If the block was read by a backend, the query will obviously fails, but if the
block was read by a
[BASE_BACKUP](https://www.postgresql.org/docs/current/protocol-replication.html#id-1.10.5.9.7.1.8.1.12)
operation (such as pg_basebackup), the command will continue its processing .
While data checkums will only catch a subset of possible problems, they still
have some values, especially if you don't trust your storage reliability.

Up to PostgreSQL 11, any checksum validation error could only be found by
looking into the logs, which clearly isn't convenient if you want to monitor
such error.

### New counters available in pg_stat_database

To make checksum errors easier to monitor, and help users to react as soon as
such a problem occurs, PostgreSQL 12 adds new counters in the
`pg_stat_database` view:

    commit 6b9e875f7286d8535bff7955e5aa3602e188e436
    Author: Magnus Hagander <magnus@hagander.net>
    Date:   Sat Mar 9 10:45:17 2019 -0800

    Track block level checksum failures in pg_stat_database

    This adds a column that counts how many checksum failures have occurred
    on files belonging to a specific database. Both checksum failures
    during normal backend processing and those created when a base backup
    detects a checksum failure are counted.

    Author: Magnus Hagander
    Reviewed by: Julien Rouhaud

&nbsp;

    commit 77bd49adba4711b4497e7e39a5ec3a9812cbd52a
    Author: Magnus Hagander <magnus@hagander.net>
    Date:   Fri Apr 12 14:04:50 2019 +0200

        Show shared object statistics in pg_stat_database

        This adds a row to the pg_stat_database view with datoid 0 and datname
        NULL for those objects that are not in a database. This was added
        particularly for checksums, but we were already tracking more satistics
        for these objects, just not returning it.

        Also add a checksum_last_failure column that holds the timestamptz of
        the last checksum failure that occurred in a database (or in a
        non-dataabase file), if any.

        Author: Julien Rouhaud <rjuju123@gmail.com>

&nbsp;

    commit 252b707bc41cc9bf6c55c18d8cb302a6176b7e48
    Author: Magnus Hagander <magnus@hagander.net>
    Date:   Wed Apr 17 13:51:48 2019 +0200

        Return NULL for checksum failures if checksums are not enabled

        Returning 0 could falsely indicate that there is no problem. NULL
        correctly indicates that there is no information about potential
        problems.

        Also return 0 as numbackends instead of NULL for shared objects (as no
        connection can be made to a shared object only).

        Author: Julien Rouhaud <rjuju123@gmail.com>
        Reviewed-by: Robert Treat <rob@xzilla.net>

Those counters will reflect checksum validation errors for both backend
activity and
[BASE_BACKUP](https://www.postgresql.org/docs/current/protocol-replication.html#id-1.10.5.9.7.1.8.1.12)
activity, per database.

{% highlight sql %}
rjuju=# \d pg_stat_database
                        View "pg_catalog.pg_stat_database"
        Column         |           Type           | Collation | Nullable | Default
-----------------------+--------------------------+-----------+----------+---------
 datid                 | oid                      |           |          |
 datname               | name                     |           |          |
 [...]
 checksum_failures     | bigint                   |           |          |
 checksum_last_failure | timestamp with time zone |           |          |
 [...]
 stats_reset           | timestamp with time zone |           |          |
{% endhighlight %}

The `checksum_failures` column will show a cumulated number of errors, and the
`checksum_last_failure` column will show the timestamp of the last checksum
failure on the database (NULL if no error ever happened).

To avoid any confusion (thanks to Robert Treat for pointing it), those two
columns will always return NULL if data checksums aren't enabled, so people
won't mistakenly think that data checksums are always successfully verified.

As a side effect, `pg_stat_database` will also now show available statistics
for shared objects (such as the `pg_database` table for instance), in a new row
with `datid` valued to **0**, and a **NULL** `datname`.  Those were always
accumulated, but weren't displayed in any system view until now.

~~A dedicated check is also [already
planned](https://github.com/OPMDG/check_pgactivity/issues/226) in
[check_pgactivity](https://opm.readthedocs.io/probes/check_pgactivity.html)!~~
A dedicated check is also [already
available](https://github.com/OPMDG/check_pgactivity/commit/0e8b516e95e4364470d4e205aebc9fe68bbcfd23)
in [check_pgactivity](https://opm.readthedocs.io/probes/check_pgactivity.html)!
