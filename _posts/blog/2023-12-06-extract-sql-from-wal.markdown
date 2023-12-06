---
layout: post
title: "Extracting SQL from WAL? (part 1)"
modified:
categories: postgresql
excerpt:
tags: [postgresql, internal]
lang: gb
image:
  feature:
date: 2023-12-06T11:04:10+08:00
---

Is it actually possible to extract SQL commands from WAL generated in "replica"
`wal_level`?

The answer is usually no, the "logical" `wal_level` exists for a reason after
all, and you shouldn't expect some kind of miracle here.

But in this series of articles you will see that if some conditions are met
you can still manage to extract some information, and how to do it.  This first
article focuses on the WAL records and how to extract the ones you want, while
the next one will show how to try to extract the information contained in those
records.

### Some context

This article is based of some work I did a few months ago to help a customer
recover some data after an incident.  It's not a perfect solution and mostly a
set of quick hacks I did to come up with something able to retrieve data in a
few hours of work only, but I hope sharing details about it and some
methodology can be helpful if you ever get in a similar situation.  You will
probably need to adapt it to your needs, with yet other hacks, but it should
give you a good start.  It can otherwise be of some interest if you want to
know a bit more about the WAL records internals and some associated
infrastructure.


### The incident

Due to a series of unfortunate events, one of their HA clusters ended in a
split-brain situation for a some time before being reinitialised, which
entirely removed one of the data directory.  After that, only the WALs that
were were generated on that instance were available, those being in "replica"
`wal_level`, and nothing else.

One possibility to try recover the data would be to restore a physical backup,
if any, replay archived WALs until the last transaction before the removed node
is promoted (assuming those are still available) and then replay the WALs
generated on that newly promoted node.  Once there you still need to look at
each row of each table of each database and compare it to yet another instance
restore from the same backup to approximately the same time as this one.
That's clearly not ideal as it will likely require many days or even weeks of
tedious hard work to do so, and will consume a lot of resources along the way.
Is there a way to do better?

After a quick discussion, it turned out that there were a few elements that
made some recovery from the WALs themselves possible (more on why later):

1. One of the data directories was still available
2. The customer guaranteed that no DDL happened since the incident
3. Only INSERTs happened during the split-brain

### WALs & Physical replication

As you probably know, postgres physical replication works by sending an exact
copy of the modified binary raw data to the various standby servers, in a
continuous stream of WAL records.  As a consequence, those records don't really
know much about the database objects they reference, and nothing about the SQL
queries that generated them.  So what do they really contain?  Let's see what's
inside the WAL records generated for an INSERT into a normal heap relation.

#### WAL records

First of all, you have to know that the WAL records are split into **Resource
Managers** (declared in
[src/include/access/rmgrlist.h](https://github.com/postgres/postgres/blob/master/src/include/access/rmgrlist.h)),
each being responsible for a specific part of postgres (heap tables, indexes,
vauum...).  They're identified by a numeric identifier and often referred to as
a `rmid`, for //resource manager identifier//.

Each of those resource managers can handle various operations, which are
internally called **opcodes**.  Here we're interested in the WAL records
generated while operating on standard heap tables, and especially during
INSERTs.  This resource manager is a bit particular as it's split into 2
different `rmid`: `RM_HEAP_ID` and R`M_HEAP2_ID`.  This is only an
implementation details, as each resource manager can only handle a limited
number of opcodes, everything is the same otherwise.

If you're curious, here's the definition of the main WAL record in the [source
code](https://github.com/postgres/postgres/blob/master/src/include/access/xlogrecord.h)
and a bit of details on the exact layout in the files:

```c
/*
 * The overall layout of an XLOG record is:
 *		Fixed-size header (XLogRecord struct)
 *		XLogRecordBlockHeader struct
 *		XLogRecordBlockHeader struct
 *		...
 *		XLogRecordDataHeader[Short|Long] struct
 *		block data
 *		block data
 *		...
 *		main data
 * [...]
 */
typedef struct XLogRecord
{
	uint32		xl_tot_len;		/* total len of entire record */
	TransactionId xl_xid;		/* xact id */
	XLogRecPtr	xl_prev;		/* ptr to previous record in log */
	uint8		xl_info;		/* flag bits, see below */
	RmgrId		xl_rmid;		/* resource manager for this record */
	/* 2 bytes of padding here, initialize to zero */
	pg_crc32c	xl_crc;			/* CRC for this record */

	/* XLogRecordBlockHeaders and XLogRecordDataHeader follow, no padding */

} XLogRecord;
```

and a block data header:

```c

/*
 * Header info for block data appended to an XLOG record.
 *
 * 'data_length' is the length of the rmgr-specific payload data associated
 * with this block. It does not include the possible full page image, nor
 * XLogRecordBlockHeader struct itself.
 *
 * Note that we don't attempt to align the XLogRecordBlockHeader struct!
 * So, the struct must be copied to aligned local storage before use.
 */
typedef struct XLogRecordBlockHeader
{
	uint8		id;				/* block reference ID */
	uint8		fork_flags;		/* fork within the relation, and flags */
	uint16		data_length;	/* number of payload bytes (not including page
								 * image) */

	/* If BKPBLOCK_HAS_IMAGE, an XLogRecordBlockImageHeader struct follows */
	/* If BKPBLOCK_SAME_REL is not set, a RelFileLocator follows */
	/* BlockNumber follows */
} XLogRecordBlockHeader;
```

Everything here is very generic as it's used by all the resource managers.  One
important bit though is the mention of a **RelFileLocator** after the block
header if the record contains information about a different relation from the
previous block, whatever is was (which is the meaning of BKPBLOCK\_SAME\_REL).
This is of course important information for us.

```c
typedef struct RelFileLocator
{
	Oid			spcOid;			/* tablespace */
	Oid			dbOid;			/* database */
	RelFileNumber relNumber;	/* relation */
} RelFileLocator;
```

But here's a first reason why you need a proper data directory to do anything
with the WALs: this doesn't contain the schema name and table name, or even the
table oid, but the **tablespace oid, database oid and relfilenode**, which is
what the WAL actually need to identify a physical relation file (which is
itself split into multiple files, the exact
[fork](https://github.com/postgres/postgres/blob/master/src/backend/storage/smgr/README)
and segment are deduced using other information).  So any table rewrite
happening since the WAL records were generated (e.g. a VACUUM FULL) and you
won't be able to identify which relation a record is about, unless of course
you find a way to map the current relfilenode to the one before the table
rewrite.

#### Heap INSERT WAL records

Now that we saw a bit of the general WAL structures, let's focus on the data
specific to an INSERT.  If you're not familiar really with the internals, one
easy way to locate the code related to a specific command is to look at the
functions associated to a resource manager.  Let's look at the **RM_HEAP_ID**
information in
[src/include/access/rmgrlist.h](https://github.com/postgres/postgres/blob/master/src/include/access/rmgrlist.h):

```c
/* symbol name, textual name, redo, desc, identify, startup, cleanup, mask, decode */
PG_RMGR(RM_HEAP_ID, "Heap", heap_redo, heap_desc, heap_identify, NULL, NULL, heap_mask, heap_decode)
```

We here have the name of the actual functions responsible for many operations
(the exact list will vary depending on the postgres major version, I'm here
using the list in postgres 17).

The **redo** function is the name of the function that applies an RM_HEAP_ID
record, the **desc** functions is the one that emits the info you see in
pg\_waldump, the **identify** function returns a string describing the opcode
and so on.  Let's look at `heap_identify()`:

```c
const char *
heap_identify(uint8 info)
{
	const char *id = NULL;

	switch (info & ~XLR_INFO_MASK)
	{
		case XLOG_HEAP_INSERT:
			id = "INSERT";
			break;
[...]
	}

	return id;
}
```

We now know that the opcode we're interested in is **XLOG_HEAP_INSERT**.  A
quick `git grep` in the tree will lead you to
[src/backend/access/heap/heapam.c](https://github.com/postgres/postgres/blob/master/src/backend/access/heap/heapam.c),
more precisely the **heap_insert** function.  The interesting bit is located in
the "XLOG stuff" block.  I will show here an extract focusing on the bit we
will need:

```c
void
heap_insert(Relation relation, HeapTuple tup, CommandId cid,
			int options, BulkInsertState bistate)
{
[...]
	/* XLOG stuff */
	if (RelationNeedsWAL(relation))
	{
		xl_heap_insert xlrec;
		xl_heap_header xlhdr;
		XLogRecPtr	recptr;
		Page		page = BufferGetPage(buffer);
		uint8		info = XLOG_HEAP_INSERT;
		int			bufflags = 0;
[...]
		xlrec.offnum = ItemPointerGetOffsetNumber(&heaptup->t_self);
		xlrec.flags = 0;
[...]
		XLogBeginInsert();
		XLogRegisterData((char *) &xlrec, SizeOfHeapInsert);

		xlhdr.t_infomask2 = heaptup->t_data->t_infomask2;
		xlhdr.t_infomask = heaptup->t_data->t_infomask;
		xlhdr.t_hoff = heaptup->t_data->t_hoff;

		/*
		 * note we mark xlhdr as belonging to buffer; if XLogInsert decides to
		 * write the whole page to the xlog, we don't need to store
		 * xl_heap_header in the xlog.
		 */
		XLogRegisterBuffer(0, buffer, REGBUF_STANDARD | bufflags);
		XLogRegisterBufData(0, (char *) &xlhdr, SizeOfHeapHeader);
		/* PG73FORMAT: write bitmap [+ padding] [+ oid] + data */
		XLogRegisterBufData(0,
							(char *) heaptup->t_data + SizeofHeapTupleHeader,
							heaptup->t_len - SizeofHeapTupleHeader);
[...]
		recptr = XLogInsert(RM_HEAP_ID, info);

		PageSetLSN(page, recptr);
	}
```

We see here that this function is as expected inserting an `RM_HEAP_ID` record,
with an `XLOG_HEAP_INSERT` opcode.  There are 2 data parts associated with this
record: the header of the tuple that's being inserted and the tuple itself.

That's great!  At this point we know how to identify what relation an INSERT is
about and the content of that INSERT.  Let's see how to filter those records
from the WALs.

### Extracting and filtering WAL records

Parsing the postgres WALs isn't that complicated but still requires to know
quite a bit more than what I showed here.  Writing such code is possible but
wait, don't we already have a tool shipped with postgres which is designed
to do exactly that?  Yes there sure is, it's
[pg_waldump](https://github.com/postgres/postgres/tree/master/src/bin/pg_waldump).

Rather that writing something similar, couldn't we simply teach pg\_waldump to
filter the records we're interested in and save them somewhere so that we can
later process them and generate SQL queries?  This way we can then also benefit
from all options in pg\_waldump like specifying the starting and/or ending LSN
or filtering a specific resource manager, without the need to worry about most
of the WAL implementation details and only focusing on the few functions
provided by postgres necessary for our need.  Let's see how to implement that.

The main source file is
[src/bin/pg_waldump/pg_waldump.c](https://github.com/postgres/postgres/blob/master/src/bin/pg_waldump/pg_waldump.c).
Skipping most of the unrelated code, we can see that there's a main loop that
takes care of reading each record one by one, optionally filter them and then
do something with them depending on how the tool was executed.  I will again
show an extract to focus on the most relevant part only:

```c
	for (;;)
	{
[...]
		/* try to read the next record */
		record = XLogReadRecord(xlogreader_state, &errormsg);
[...]
		/* apply all specified filters */
		if (config.filter_by_rmgr_enabled &&
			!config.filter_by_rmgr[record->xl_rmid])
			continue;

[...]

		/* perform any per-record work */
		if (!config.quiet)
		{
			if (config.stats == true)
			{
				XLogRecStoreStats(&stats, xlogreader_state);
				stats.endptr = xlogreader_state->EndRecPtr;
			}
			else
				XLogDumpDisplayRecord(&config, xlogreader_state);
		}

		/* save full pages if requested */
		if (config.save_fullpage_path != NULL)
			XLogRecordSaveFPWs(xlogreader_state, config.save_fullpage_path);

		/* check whether we printed enough */
		config.already_displayed_records++;
		if (config.stop_after_records > 0 &&
			config.already_displayed_records >= config.stop_after_records)
			break;
	}
```

That's quite simple, pg\_waldump read the records one by one until it needs to
stop, ignore the records that the users asked to discard and then takes action
on the remaining ones.  We can see that there's already an option to save full
page images, it definitely looks like we could just add something similar
there, but for all records.

First, we will need to provide a way to identify the relation the INSERT is
about.  That's the `RelFileLocator`, and we already know that it can be found
just after the XLogRecordBlockHeader.  Postgres provides a function to retrieve
this information, and a bit more, named
[`XLogRecGetBlockTagExtended()`](https://github.com/postgres/postgres/blob/master/src/backend/access/transam/xlogreader.c).
Here is it's description:

```c
/*
 * Returns information about the block that a block reference refers to,
 * optionally including the buffer that the block may already be in.
 *
 * If the WAL record contains a block reference with the given ID, *rlocator,
 * *forknum, *blknum and *prefetch_buffer are filled in (if not NULL), and
 * returns true.  Otherwise returns false.
 */
bool
XLogRecGetBlockTagExtended(XLogReaderState *record, uint8 block_id,
						   RelFileLocator *rlocator,
 						   ForkNumber *forknum,
						   BlockNumber *blknum,
						   Buffer *prefetch_buffer)
```

We need to provide the record - pg\_waldump already retrieves it for us - and
the `block_id`.  The `block_id`, or block reference, is simply an offset in the
array of data that the WAL records contains.  If you look a bit above in this
article, you will see that we already know that `heap_insert()` only uses a
hardcoded **0** block\_id: this is the first argument in the various
`XLogRegisterXXX()` function calls.

Next we need to retrieve the actual WAL record data, the tuple header and the
tuple itself.  This one is a bit trickier, as the record can either be found in
a simple WAL record or in a full-page record.  We need to check for a simple
WAL record first.  The associated function is
[`XLogRecGetBlockData()`](https://github.com/postgres/postgres/blob/master/src/backend/access/transam/xlogreader.c):

```c
/*
 * Returns the data associated with a block reference, or NULL if there is
 * no data (e.g. because a full-page image was taken instead). The returned
 * pointer points to a MAXALIGNed buffer.
 */
char *
XLogRecGetBlockData(XLogReaderState *record, uint8 block_id, Size *len)
```

As noted in the comment, if the function returns NULL (and sets len to **0**)
then the data may be in a full-page image instead (or the data could be missing
entirely).  If that's the case we need to retrieve the full-page image, and
then locate the tuple the INSERT was about and extract it in the same format as
a simple WAL record.

Postgres provides a function to extract the full-page image:
[`RestoreBlockImage()`](https://github.com/postgres/postgres/blob/master/src/backend/access/transam/xlogreader.c):

```c
/*
 * Restore a full-page image from a backup block attached to an XLOG record.
 *
 * Returns true if a full-page image is restored, and false on failure with
 * an error to be consumed by the caller.
 */
bool
RestoreBlockImage(XLogReaderState *record, uint8 block_id, char *page)
```

which is straightforward to use: just provide the record and the block
identifier and you get the full-page image if found.  However, there's no
function available to extract a tuple for a full-page image.  Indeed postgres
can simply overwrite the whole block with the full-page image as it contains
the latest version of the block at the time it was generated, but in our case
we definitely don't want to emit an INSERT statement for every already existing
tuple in the block!

Fortunately, even when we get a full-page image, our record still contains a
//main data area//.  If you look up at the `heap_insert()` function, that's
the call to `XLogRegisterData()`, and as you see here it contains an
`xl_heap_insert` struct.  And the first member of this struct, **offnum**, is
actually the position of the tuple in the page which is exactly what we need!

With all of that, it's just a matter of accessing the tuple header and tuple at
the correct place among all the tuples present in the page, and save as we
would way it would be if it were a simple WAL record.  If you're wondering how
exactly it should be done, you can always look at how postgres itself does it
when it needs to return a specific tuple and adapt that code to your need.  The
functions responsible for that are `heapgetpage()` and `heapgettup()`, located
in the
[src/backend/access/heap/heapam.c](https://github.com/postgres/postgres/blob/master/src/backend/access/heap/heapam.c)
file we already mentioned.

We now have the information about the physical file location and the record
itself that we will need to transmit to another program to decode it.  The best
way to do that is to simply save the record as-is in a binary file, and use the
file name to transmit the metadata.  I chose the following pattern to name the
produced files:

    LSN.TABLESPACE_OID.DATABASE_OID.RELFILENODE.FORKNAME

It will be trivial for the consumer to parse it and extract the required
metadata.  One thing to note is that I don't put the `rmid` or the `opcode`
here as I'm only emitting the only one I'm interested in and discard everything
else.  If that's not your case you should definitely remember to add those in
the filename pattern.

Since this requires a bit of code to implement, I won't detail it here but you
can find the full result in the patch for pg\_waldump that I'm attaching to
this article, which implements this as a new **--save-records** option.

To conclude, let me also remind you that a compiled version of pg\_waldump will
only work for a single major postgres version.  In my case, I had to work with
postgres 11, so you can [find the patch for this version
here](/assets/patch/0001-Add-a-save-records-PATH-option-to-pg_waldump_pg11.patch),
but if needed I also rebased it again the current commit on the master branch,
which [can be found
here](/assets/patch/0001-Add-a-save-records-PATH-option-to-pg_waldump_pg17.patch).

### What's next?

This is the end of this first article.  We saw some details on the postgres WAL
infrastructure, with a full example for the case of a plain INSERT on a heap
table.  We also learned where to look to find where other WAL records are
generated and to see more details about the implementation.

We also checked how pg\_waldump is working and how to adapt it for our need,
with a provided complete patch for both [postgres
11](/assets/patch/0001-Add-a-save-records-PATH-option-to-pg_waldump_pg11.patch)
and [the current dev version (postgres
17)](/assets/patch/0001-Add-a-save-records-PATH-option-to-pg_waldump_pg17.patch).
Again, I'd like to remind you that all this work is only at a proof-of-concept
stage, it's definitely not polished and I'm sure that are many problems that
would need to be fixed.  One obvious example of such problem is that we're
saving all INSERT we find in the logs but we don't check if the transaction
they're in eventually committed.  It would be possible to fix that but it would
require extraneous code, so as is it's up to the users to double check that as
needed.  Overall it was enough to recover the needed data so I didn't pursue
any more work on it.

In the next article we will see some usage of this new **--save-records**
option, and also how to read those records and decode them to generate plain
INSERT queries.  Stay tuned!
