---
layout: post
title: "New in pg12: New leader_pid column in pg_stat_activity"
modified:
categories: postgresql
excerpt:
tags: [postgresql, monitoring, pg13, new_feature]
lang: gb
image:
  feature:
date: 2020-02-06T14:59:53+02:00
---

### New leader_pid column in pg_stat_activity view

Surprisingly, since parallel query was introduced in PostgreSQL 9.6, it was
impossible to know wich backend a parallel worker was related to.  So, as
[Guillaume pointed
out](https://twitter.com/g_lelarge/status/1209486212190343168), it makes it
quite difficult to build simple tools that can sample the wait events related
to all process involved in a query.  A simple solution to that problem is to
export the `lock group leader` information available in the backend at the SQL
level:

    commit b025f32e0b5d7668daec9bfa957edf3599f4baa8
    Author: Michael Paquier <michael@paquier.xyz>
    Date:   Thu Feb 6 09:18:06 2020 +0900

    Add leader_pid to pg_stat_activity

    This new field tracks the PID of the group leader used with parallel
    query.  For parallel workers and the leader, the value is set to the
    PID of the group leader.  So, for the group leader, the value is the
    same as its own PID.  Note that this reflects what PGPROC stores in
    shared memory, so as leader_pid is NULL if a backend has never been
    involved in parallel query.  If the backend is using parallel query or
    has used it at least once, the value is set until the backend exits.

    Author: Julien Rouhaud
    Reviewed-by: Sergei Kornilov, Guillaume Lelarge, Michael Paquier, Tomas
    Vondra
    Discussion: https://postgr.es/m/CAOBaU_Yy5bt0vTPZ2_LUM6cUcGeqmYNoJ8-Rgto+c2+w3defYA@mail.gmail.com

With this change, you can now easily find all processes involved in a parallel
query.  For instance:

{% highlight sql %}
=# SELECT query, leader_pid,
  array_agg(pid) filter(WHERE leader_pid != pid) AS members
FROM pg_stat_activity
WHERE leader_pid IS NOT NULL
GROUP BY query, leader_pid;
       query       | leader_pid |    members
-------------------+------------+---------------
 select * from t1; |      31630 | {32269,32268}
(1 row)

{% endhighlight %}

Be careful, as mentionned in the commit message, if the `leader_pid` is the
same as `pid`, it doesn't necessarily mean that the backend is currently
performing a parallel query, as once set this field is never reset.  Also, to
avoid extra ovherhead, no additional lock is held while outputting the data.
It means that each row is processed independently.  So, while quite unlikely,
you can get in some circumstances inconsistent data, such as a parallel worker
pointing to a pid that already disconnected.
