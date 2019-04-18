---
layout: post
title: "Nouveauté pg12: Statistiques sur les erreurs de checkums"
modified:
categories: postgresqlfr
excerpt:
tags: [postgresql, monitoring, pg12, new_feature]
lang: fr
image:
  feature:
date: 2019-04-18T13:02:26+02:00
---

### Data checksums

Ajoutés dans [PostgreSQL
9.3](https://git.postgresql.org/gitweb/?p=postgresql.git;a=commitdiff;h=96ef3b8ff1c),
les [data
checksums](https://www.postgresql.org/docs/current/app-initdb.html#APP-INITDB-DATA-CHECKSUMS)
peuvent aider à détecter les corruptions de données survenant sur votre
stockage.

Les checksums sont activés si l'instance a été initialisée en utilisant `initdb
--data-checksums` (ce qui n'est pas le comportement par défaut), ou s'ils ont
été activés après en utilisant la nouvelle utilitaire
activated afterwards with the new
[pg_checksums](https://www.postgresql.org/docs/devel/app-pgchecksums.html)
également [ajouté dans PostgreSQL
12](https://git.postgresql.org/gitweb/?p=postgresql.git;a=commitdiff;h=ed308d783790).

Quand les checksums sont ativés, ceux-ci sont écrits à chaque fois qu'un bloc
de données est écrit sur disque, et vérifiés à chaque fois qu'un bloc est lu
depuis le disque (ou depuis le cache du système d'exploitation).  Si la
vérification échoue, une erreur est remontée dans les logs.  Si le bloc était
lu par un processus client, la requête associée échouera bien évidemment, mais
si le bloc était lu par une opération
[BASE_BACKUP](https://www.postgresql.org/docs/current/protocol-replication.html#id-1.10.5.9.7.1.8.1.12)
(tel que pg_basebackup), la commande continuera à s'exécuter.  Bien que les
data checksums ne détecteront qu'un sous ensemble des problèmes possibles, ils
ont tout de même une certaine utilisé, surtout si vous ne faites pas confiance
à votre stockage.

Jusqu'à PostgreSQL 11, les erreurs de validation de checksum ne pouvaient être
trouvées qu'en cherchant dans les logs, ce qui n'est clairement pas pratique si
vous voulez monitorer de telles erreurs.

### Nouveaux compteurs disponibles dans pg_stat_database

Pour rendre la supervision des erreurs de checksum plus simple, et pour aider
les utilisateurs à réagir dès qu'un tel problème survient, PostgreSQL 12 ajoute
de nouveaux compteurs dans la vue `pg_stat_database` :

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

Ces compteurs reflèteront les erreurs de validation de checksum à la fois pour
les processus clients et pour l'activité
[BASE_BACKUP](https://www.postgresql.org/docs/current/protocol-replication.html#id-1.10.5.9.7.1.8.1.12),
par base de données.

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

La colonne `checksum_failures` montrera un nombre cumulé d'erreurs, et la
colonne `checksum_last_failure` montrera l'horodatage de la dernière erreur de
validation sur la base de données (NULL si aucune erreur n'est jamais
survenue).

Pour éviter toute confusion (merci à Robert Treat pour l'avoir signalé), ces
deux colonnes retourneront toujours NULL si les data checkums ne sont pas
activés, afin qu'on ne puisse pas croire que les checksums sont toujours
vérifiés avec succès.

Comme effet de bord, `pg_stat_database`  montrera maintenant également les
statistiques disponibles pour les objets partagés (tels que la table
`pg_database` par exemple), dans une nouvelle ligne pour laquelle `datid` vaut
**0**, et `datname` vaut **NULL**.

Une sonde dédiée est également [déjà
planifiée](https://github.com/OPMDG/check_pgactivity/issues/226) dans
[check_pgactivity](https://opm.readthedocs.io/probes/check_pgactivity.html)!
