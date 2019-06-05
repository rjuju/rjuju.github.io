---
layout: post
title: "PoWA 4: nouveautés dans powa-archivist !"
modified:
categories: postgresqlfr
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: fr
image:
  feature:
date: 2019-06-05T16:26:17+02:00
---

Cet article fait partie d'une série d'article sur [la beta de PoWA
4](http://powa.readthedocs.io/), et décrit les changements présents dans
[powa-archivist](https://powa.readthedocs.io/en/latest/components/powa-archivist/index.html).

Pour plus d'information sur cette version 4, vous pouvez consulter [l'article
de présentation général]({% post_url
blog/2019-05-17-powa-4-avec-mode-remote-disponible-en-beta %}).


### Aperçu rapide

Tout d'abord, il faut savoir qu'il n'y a pas d'upgrade possible depuis la v3
vers la v4, il est donc nécessaire d'effectuer un `DROP EXTENSION powa` si vous
utilisiez déjà PoWA sur vos serveurs.  Cela est du au fait que la v4 apporte
**de très nombreux** changements dans la partie SQL de l'extension, ce qui en
fait le changement le plus significatif dans la suite PoWA pour cette nouvelle
version.  Au moment où j'écris cet article, la quantité de changements apportés
dans cette extension est :

{% highlight diff %}
 CHANGELOG.md       |   14 +
 powa--4.0.0dev.sql | 2075 +++++++++++++++++++++-------
 powa.c             |   44 +-
 3 files changed, 1629 insertions(+), 504 deletions(-)
{% endhighlight %}

L'absence d'upgrade ne devrait pas être un problème en pratique.  PoWA est un
outil pour analyser les performances, il est fait pour avoir des données avec
une grande précision mais un historique très limité.  Si vous cherchez une
solution de supervision généraliste pour conserver des mois de données, PoWA
n'est définitivement pas l'outil qu'il vous faut.

### Configurer la liste des *serveurs distants*

En ce qui concerne les changements à proprement parler, le premier petit
changement est que le [background
worker](https://www.postgresql.org/docs/current/bgworker.html) n'est plus
nécessaire pour le fonctionnement de powa-archivist, car il n'est pas utilisé
pour le mode distant.  Cela signifie qu'un redémarrage de PostgreSQL n'est plus
nécessaire pour installer PoWA.  Bien évidemment, un redémarrage est toujours
nécessaire si vous souhaitez utiliser le mode local, en utilisant le background
worker, or si vous voulez installer des extensions additionelles qui
nécessitent elles-même un redémarrage.

Ensuite, comme PoWA requiert un peu de configuration (fréquence des snapshot,
rétention des données et ainsi de suite), certaines nouvelles tables sont
ajouter pour permettre de configurer tout ça.  La nouvelle table `powa_servers`
stocke la configuration de toutes les instances distantes dont les données
doivent être stockées sur cette instance.  Cette *instance PoWA locale* est
appelée un **serveur repository** (qui devrait typiquement être dédiée à
stocker des données PoWA), en opposition aux **instances distantes** qui sont
les instances que vous voulez monitorer.  Le contenu de cette table est tout ce
qu'il y a de plus simple :

{% highlight sql %}
\d powa_servers
                              Table "public.powa_servers"
  Column   |   Type   | Collation | Nullable |                 Default
-----------+----------+-----------+----------+------------------------------------------
 id            | integer  |           | not null | nextval('powa_servers_id_seq'::regclass)
 hostname      | text     |           | not null |
 alias         | text     |           |          |
 port          | integer  |           | not null |
 username      | text     |           | not null |
 password      | text     |           |          |
 dbname        | text     |           | not null |
 frequency     | integer  |           | not null | 300
 powa_coalesce | integer  |           | not null | 100
 retention     | interval |           | not null | '1 day'::interval
{% endhighlight %}

Si vous avez déjà utilisé PoWA, vous devriez reconnaître la plupart des options
de configuration qui sont maintenant stockées ici.  Les nouvelles options sont
utilisées pour décrire comment se connecter aux *instances distances*, et
peuvent fournir un alias à afficher sur l'UI.

Vous avez également probablement remarqué une colonne **password**.  Stocker un
mot de passe en clair dans cette table est une hérésie pour n'importe qui
désirant un minimum de sécurité.  Ainsi, comme mentionné dans la [section
sécurité de la documentation de PoWA
](https://powa.readthedocs.io/en/latest/security.html#connection-on-remote-servers),
vous pouvez stocker NULL pour le champ password et à la place utiliser
[n'importe laquelle des autres méthodes d'authentification supportée par la
libpq](https://www.postgresql.org/docs/current/auth-methods.html)
(fichier .pgpass, certificat...).  Une authentification plus sécurisée est
chaudement recommandée pour toute installation sérieuse.

Une autre table, la table `powa_snapshot_metas`, est également ajoutée pour
stocker quelques métadonnées concernant les informations de snapshot pour
chaque *serveur distant*.

{% highlight sql %}
                                   Table "public.powa_snapshot_metas"
    Column    |           Type           | Collation | Nullable |                Default
--------------+--------------------------+-----------+----------+---------------------------------------
 srvid        | integer                  |           | not null |
 coalesce_seq | bigint                   |           | not null | 1
 snapts       | timestamp with time zone |           | not null | '-infinity'::timestamp with time zone
 aggts        | timestamp with time zone |           | not null | '-infinity'::timestamp with time zone
 purgets      | timestamp with time zone |           | not null | '-infinity'::timestamp with time zone
 errors       | text[]
{% endhighlight %}

Il s'agit tout simplement d'un compteur pour compter le nombre de snapshots
effectués, un timestamp pour chaque type d'événement survenu (snapshot,
aggrégation et purge) et un tableau de chaîne de caractères pour stocker toute
erreur survenant durant le snapshot, afin que l'UI pour l'afficher.

### API SQL pour configurer les *serveurs distants*

Bien que ces tables soient très simples, une [API SQL basique est disponible
pour déclarer de nouveaux serveurs et les
configurer](https://powa.readthedocs.io/en/latest/remote_setup.html#configure-powa-and-stats-extensions-on-each-remote-server).
6 fonctions de bases sont disponibles :

  * `powa_register_server()`, pour déclarer un nouveau *servuer distant*, ainsi
    que la liste des extensions qui y sont disponibles
  * `powa_configure_server()` pour mettre à jour un des paramètres pour le
    *serveur distant* spécifié (en utilisant un paramètre JSON, où la clé est
    le nom du paramètre à changer et la valeur la nouvelle valeur à utiliser)
  * `powa_deactivate_server()` pour désactiver les snapshots pour le *serveur
    distant* spécifiqué (ce qui concrètement positionnera le paramètre
    `frequency` à **-1**)
  * `powa_delete_and_purge_server()` pour supprimer le *serveur distant*
    spécifié de la liste des serveurs et supprimer toutes les données associées
    aux snapshots
  * `powa_activate_extension()`, pour déclarer qu'une nouvelle extension est
    disponible sur le *serveur distant* spécifié
  * `powa_deactivate_extension()`, pour spécifier qu'une extension n'est plus
    disponible sur le *serveur distant* spécifié

Toute action plus compliquée que ça devra être effectuée en utilisant des
requêtes SQL.  Heureusement, il ne devrait pas y avoir beaucoup d'autres
besoins, et les tables sont vraiment très simple donc cela ne devrait pas poser
de soucis.  [N'hésitez cependant pas à demander de nouvelles
fonctions](https://github.com/powa-team/powa-archivist/issues) si vous aviez
d'autres besoins.  Veuillez également noter que l'UI ne vous permet pas
d'appeler ces fonctions, puisque celle-ci est pour le moment **entièrement en
lecture seule**.

### Effectuer des *snapshots distants*

Puisque les métriques sont maintenant stockées sur une instance PostgreSQL
différente, nous avons énormément changé la façon dont les *snapshots*
(récupérer les données fournies par une [extensions
statistique](https://powa.readthedocs.io/en/latest/components/stats_extensions/index.html)
et les stockées dans le catalogue PoWA [de manière à optimiser le stockage]({%
post_url blog/2019-04-06-minimiser-le-surcout-de-stockage-par-ligne %})) sont
effectués.

La liste de toutes les extensions statistiques, ou *sources de données*, qui
sont disponibles sur un **serveur** (soit *distant* soit *local*) et pour
lesquelles un *snapshot* devrait être effectué est stockée dans une table
appelée `powa_functions`:

{% highlight sql %}
               Table "public.powa_functions"
     Column     |  Type   | Collation | Nullable | Default
----------------+---------+-----------+----------+---------
 srvid          | integer |           | not null |
 module         | text    |           | not null |
 operation      | text    |           | not null |
 function_name  | text    |           | not null |
 query_source   | text    |           |          |
 added_manually | boolean |           | not null | true
 enabled        | boolean |           | not null | true
 priority       | numeric |           | not null | 10
{% endhighlight %}

Un nouveau champ `query_source` a été rajouté.  Celui-ci fournit le nom de la
*fonction source*, nécessaire pour la compatibilité d'une [extension
statistique](https://powa.readthedocs.io/en/latest/components/stats_extensions/index.html)
avec les snapshots distants.  Cette fonction est utilisée pour exporter les
compteurs fournis par cette extension sur un serveur différent, dans une *table
transitoire* dédiée.  La fonction de *snapshot* effectuera alors le *snapshot*
en utilisant automatiquement ces données exportées plutôt que celles fournies
par l'extension statististique locale quand le mode distant est utilisé.  Il
est à noter que l'export de ces compteurs ainsi que le snapshot distant est
effectué automatiquement par le nouveau [daemon
powa-collector](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
que je présenterai dans un autre article.

Voici un exemple montant comment PoWA effectue un *snapshot distant* d'une
liste de base données.  Comme vous allez le voir, c'est très simple ce qui
signifie qu'il est également très simple d'ajouter cette même compatibilité
pour une nouvelle extension statistique.

La *table transitoire*:

{% highlight sql %}
   Unlogged table "public.powa_databases_src_tmp"
 Column  |  Type   | Collation | Nullable | Default
---------+---------+-----------+----------+---------
 srvid   | integer |           | not null |
 oid     | oid     |           | not null |
 datname | name    |           | not null |
{% endhighlight %}

Pour de meilleurs performances, toutes les *tables transitoires* sont **non
journalisées (unlogged)**, puisque leur contenu n'est nécessaire que durant un
*snapshot* et sont supprimées juste après.  Dans cet examlple, la *table
transitoire* ne stocke que l'identifiant du serveur distant correspondant à ces
données, l'oid ainsi que le nom de chacune des bases de données présentes sur
le *serveur distant*.

Et la *fonction source* :

{% highlight sql %}
CREATE OR REPLACE FUNCTION public.powa_databases_src(_srvid integer,
    OUT oid oid, OUT datname name)
 RETURNS SETOF record
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF (_srvid = 0) THEN
        RETURN QUERY SELECT d.oid, d.datname
        FROM pg_database d;
    ELSE
        RETURN QUERY SELECT d.oid, d.datname
        FROM powa_databases_src_tmp d
        WHERE srvid = _srvid;
    END IF;
END;
$function$
{% endhighlight %}

Cette fonction retourne simplement le contenu de `pg_database` si les données
locales sont demandées (l'identifiant de serveur **0** est toujours le serveur
local), ou alors le contenu de la *table transitoire* pour le serveur distant
spécifié.

La *fonction de snapshot* peut alors facilement effectuer n'importe quel
traitement avec ces données pour le *serveur distant* voulu.  Dans le cas de la
fonction `powa_databases_snapshot()`, il s'agit simplement de synchroniser la
liste des bases de données, et de stocker le timestamp de suppression si une
base de données qui existait précédemment n'est plus listée.

Pour plus de détails, vous pouvez consulter la documentation concernant
[l'ajout d'une source de données dans
PoWA](https://powa.readthedocs.io/en/latest/components/powa-archivist/development.html),
qui a été mise à jour pour les spécificités de la version 4.
