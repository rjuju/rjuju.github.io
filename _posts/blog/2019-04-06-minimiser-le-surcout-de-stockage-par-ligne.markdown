---
layout: post
title: "Minimiser le surcoût de stockage par ligne"
modified:
categories: postgresqlfr
excerpt:
tags: [postgresql, performance, PoWA]
lang: fr
image:
  feature:
date: 2019-04-06T09:51:28+02:00
---

J'entends régulièrement des complaintes sur la quantité d'espace disque gâchée
par PostgreSQL pour chacune des lignes qu'il stocke.  Je vais essayer de
montrer ici quelques astuces pour minimiser cet effet, afin d'avoir un stockage
plus efficace.

### Quel surcoût ?

Si vous n'avez pas de table avec plus que quelques centaines de millions de
lignes, il est probable que ce n'est pas un problème pour vous.

Pour chaque ligne stockée, postgres conservera quelques données additionnelles
pour ses propres besoins.  C'est [documenté
ici](https://www.postgresql.fr/docs/current/storage-page-layout.html#heaptupleheaderdata-table).
La documentation indique :

| Field       | Type            | Length  | Description                                                               |
|-------------|-----------------|---------|---------------------------------------------------------------------------|
| t_xmin      | TransactionId   | 4 bytes | XID d'insertion                                                           |
| t_xmax      | TransactionId   | 4 bytes | XID de suppresion                                                         |
| t_cid       | CommandId       | 4 bytes | CID d'insertion et de suppression (surcharge avec t_xvac)                 |
| t_xvac      | TransactionId   | 4 bytes | XID pour l'opération VACUUM déplaçant une version de ligne                |
| t_ctid      | ItemPointerData | 6 bytes | TID en cours pour cette version de ligne ou pour une version plus récente |
| t_infomask2 | uint16          | 2 bytes | nombre d'attributs et quelques bits d'état                                |
| t_infomask  | uint16          | 2 bytes | différents bits d'options (flag bits)                                     |
| t_hoff      | uint8           | 1 byte  | décalage vers les données utilisateur                                     |

Ce qui représente **23 octets** sur la plupart des architectures (il y a soit
**t_cid** soit **t_xvac**).

Vous pouvez d'ailleurs consulter une partie de ces champs grâce aux colonnes
cachées présentes dans n'importe quelle table en les ajoutant dans la partie
SELECT d'une requête, ou en cherchant pour les numéros d'attribut négatifs dans
le catalogue **pg_attribute** :

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

Si vous comparez ces champs avec le tableau précédent, vous pouvez constater
que toutes ces colonnes ne sont pas stockées sur disque.  Bien évidemment,
PostgreSQL ne stocke pas l'oid de la table pour chaque ligne.  Celui-ci est
ajouté après, lors de la construction d'une ligne.

Si vous voulez plus de détails techniques, vous pouvez regarder
[htup_detail.c](http://doxygen.postgresql.org/htup__details_8h.html), en
commençant par
[TupleHeaderData struct](http://doxygen.postgresql.org/structHeapTupleHeaderData.html).

### Combien est-ce que ça coûte ?

Puisque ce surcoût est fixe, plus la taille des lignes croît plus il devient
négligeable.  Si vous ne stocker qu'une simple colonne de type intt (**4
octets**), chaque ligne nécessitera :

{% highlight C %}
23B + 4B = 27B
{% endhighlight %}

soit **85% de surcoût**, ce qui est plutôt horrible.

D'une autre côté, si vous stockez 5 integer, 3 bigint et 2 colonnes de type
texte (disons environ 80 octets en moyenne), cela donnera :

{% highlight C %}
23B + 5*4B + 3*8B + 2*80B = 227B
{% endhighlight %}

C'est "seulement" **10% de surcoût**.

### Et donc, comment minimiser ce surcoût

L'idée est de stocker les même données, mais avec moins d'enregistrements.
Comment faire ?  En aggrégeant les données dans des tableaux.  Plus vous mettez
d'enregistrements dans un seul tableau, plus vous minimiserez le surcoût.  Et
si vous aggrégez suffisamment de données, vous pouvez bénéficier d'une
compression entièrement transparente grâce au [mécanisme de
TOAST](https://www.postgresql.fr/docs/current/storage-toast.html).

Voyons ce que cela donne avec une table ne disposant que d'une seule colonne,
avec 10 millions de lignes :

{% highlight sql %}
# CREATE TABLE raw_1 (id integer);

# INSERT INTO raw_1 SELECT generate_series(1,10000000);

# CREATE INDEX ON raw_1 (id);
{% endhighlight %}

Les données utilisateur ne devrait nécessiter que 10M * 4 octets, soit environ
**30 Mo**, alors que cette table pèse **348 Mo**.  L'insertion des données
prend environ **23 secondes**.

**NOTE :** Si vous faites le calcul, vous trouverez que le surcoût est d'un peu
plus que **32 octets** par ligne, pas **23 octets**.  C'est parce que chaque
bloc de données a également un surcoût, une gestion des colonnes NULL ainsi que
des contraintes d'alignement.  Si vous voulez plus d'informations à ce sujet,
je vous recommande de regarder [cette
présentation](https://github.com/dhyannataraj/tuple-internals-presentation)
{: .notice}

Comparons maintenant cela avec la version aggrégées des même données :

{% highlight sql %}
# CREATE TABLE agg_1 (id integer[]);

# INSERT INTO agg_1 SELECT array_agg(i)
FROM generate_series(1,10000000) i
GROUP BY i % 2000000;

# CREATE INDEX ON agg_1 (id);
{% endhighlight %}

Cette requête insèrera 5 éléments par ligne.  J'ai fait le même test avec 20,
100, 200 et 1000 éléments par ligne.  Les résultats sont les suivants :

[![Benchmark 1](/images/tuple_overhead_1.svg)](/images/tuple_overhead_1.svg)


**NOTE :** La taille pour 1000 éléments par ligne est un peu plus importante
que pour la valeur précédents.  C'est parce que c'est le seul qui implique une
taille suffisamment importante pour être TOAST-ée, mais pas assez pour être
compressée.  On peut donc voir ici un peu de surcoût lié au TOAST.
{: .notice}

Jusqu'ici tout va bien, on peut voir de plutôt bonnes améliorations à la fois
sur la taille et sur le temps d'insertion, même pour les tableaux les plus
petits.  Voyons maintenant l'impact pour récupérer des lignes.  Je testerai la
récupération de toutes les lignes, ainsi qu'une seule ligne au moyen d'un
parcours d'index (j'ai utilisé pour les tests EXPLAIN ANALYZE afin de minimiser
le temps passé par psql à afficher les données) :
psql):

{% highlight sql %}
# SELECT id FROM raw_1;

# CREATE INDEX ON raw_1 (id);

# SELECT * FROM raw_1 WHERE id = 500;
{% endhighlight %}

Pour correctement indexer le tableau, nous avons besoin d'un index GIN.  Pour
récupérer les valeurs de toutes les données aggrégées, il est nécessaire
d'appeler unnest() sur le tableau, et pour récupérer un seul enregistrement il
faut être un peu plus créatif :

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

Voici le tableau comparant les temps de création de l'index ainsi que la taille
de celui-ci, pour chaque dimension de tableau :

[![Benchmark 2](/images/tuple_overhead_2.svg)](/images/tuple_overhead_2.svg)

L'index GIN est un peu plus que deux fois plus volumineux que l'index btree, et
si on accumule la taille de la table à la taille de l'index, la taille totale
est presque identique avec ou sans aggrégation.  Ce n'est pas un gros problème
puisque cet exemple est très naïf, et nous verrons juste après comme éviter
d'avoir recours à un index GIN pour conserver une taille totale faible.  De
plus, l'index est bien plus lent à créer, ce qui signifie qu'INSERT sera
également plus lent.

Voici le tableau comparant le temps pour récupérer toutes les lignes ainsi
qu'une seule ligne :

[![Benchmark 3](/images/tuple_overhead_3.svg)](/images/tuple_overhead_3.svg)

Récupérer toutes les lignes n'est probablement pas un exemple intéressant, mais
il est intéressant de noter que dès que le tableau contient suffisamement
d'éléments cela devient plus efficace que faire la même chose avec la table
originale.  Nous voyons également que récuérer un seul élément est bien plus
rapide qu'avec l'index btree, grâce à l'efficacité de GIN.  Ce n'est pas testé
ici, mais puisque seul les index btree sont nativement triés, si vous devez
récupérer un grand nombre d'enregistrements triés, l'utilisation d'un index GIN
nécessitera un tri supplémentaire, ce qui sera bien plus lent qu'un simple
parcours d'index btree.

### Un exemple plus réaliste

Maintenant que nous avons vu les bases, voyons comment aller un peu plus loin :
aggréger plus d'une colonne et éviter d'utiliser trop d'espce disque (et de
ralentissements à l'écriture) du fait d'un index GIN.  Pour cela, je vais
présenter comme [PoWA](https://powa.readthedocs.io/) stocke ses données.

Pour chaque source de données collectée, deux tables sont utilisées : une pour
les données **historiques et aggrégées**, ainsi qu'une pour **les données
courantes**.  Ces tables stockent les données dans un type de données
personnalisé plutôt que des colonnes.  Voyons les tables liées à l'extension
**pg_stat_statements** :

Le type de données, grosso modo tous les compteurs présents dans
pg_stat_statements ainsi que l'horodatage associé à l'enregistrement :

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

La table pour les données courrante stocke l'identifieur unique de
pg_stat_statements (queryid, dbid, userid), ainsi qu'un enregistrement de
compteurs :

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

La table pour les données aggrégées contient le même identifieur unique, un
tableau d'enregistrements ainsi que quelques champs spéciaux :

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

Nous stockons également l'intervalle d'horodatage (*coalesce_range*) contenant
tous les compteurs aggrégés dans la ligne, ainsi que les valeurs minimales et
maximales de chaque compteurs dans deux compteurs dédiés.  Ces champs
supplémentaires ne consomment pas trop d'espace, et permettent une indexation
ainsi qu'un traitement très efficace, basé sur les modèles d'accès aux données
de l'application associée.

Cette table est utilisée pour savoir combien de ressources ont été utilisée par
une requête sur un intervalle de temps donné.  L'index GiST ne sera pas très
gros puisqu'il n'indexe que deux petites valeus  pour X compteurs aggrégés, et
trouvera les lignes correspondant à une requête et un intervalle de temps
données de manière très efficace.

Ensuite, calculer les ressources consommées peut être fait de manière très
efficace, puisque les compteurs de pg_stat_statements sont strictement
monotones.  L'algorithme pourrait être :

* si l'intervalle de temps de la ligne est entièrement contenu dans
  l'intervalle de temps demandé, nous n'avons besoin de calculer que le delta
  du résumé de l'enregistrement :
  **maxs_in_range.counter - mins_in_range.counter**
* sinon (c'est-à-dire pour uniquement deux lignes par queryid) nous dépilons le
  tableau, filtrons les enregistrements qui ne sont pas compris dans
  l'intervalle de temps demandé, conservons la première et dernière valeur et
  calculons pour chaque compteur le maximum moins le minimum.


**NOTE :** Dans les faits, l'interface de PoWA dépilera toujours tous les
enregistrements contenus dans l'intervalle de temps demandé, puisque
l'interface est faite pour montrer l'évolution de ces compteurs sur un
intervalle de temps relativement réduit, mais avec une grande précision.
Heureusement, dépiler les tableaux n'est pas si coûteux que ça, surtout en
regard de l'espace disque économisé.
{: .notice}

Et voici la taille nécessaire pour les valeurs aggrégées et non aggrégées.
Pour cela j'ai laissé PoWA générer **12 331 366 enregistrements** (en
configurant une capture toutes les 5 secondes pendant quelques heures, et avec
l'aggrégation par défaut de 100 enregistrements par lignes), et créé un index
btree sur (queryid, ((record).ts) pour simuler l'index présent sur les tables
aggrégées :

[![Benchmark 4](/images/tuple_overhead_4.svg)](/images/tuple_overhead_4.svg)

Vous trouvez aussi que c'est plutôt efficace ?

### Limitations

Il y a quelques limitations avec l'aggrégation d'enregistrements.  Si vous
faites ça, vous ne pouvez plus garantir de contraintes telles que des clés
étrangères ou contrainte d'unicité.  C'est donc à utiliser pour des données non
relationnelles, telles que des compteurs ou des métadonnées.

### Bonus

L'utilisation de type de données personnalisés vous permet de faire des choses
sympathiques, comme définir des **opérateurs personnalisés**.  Par exemple, la
version 3.1.0 de PoWA fournit deux opérateurs pour chacun des types de données
personnalisé définis :

* l'opérateur **-**, pour obtenir la différent entre deux enregistrements
* l'opérateur **/**, pour obtenir la différence *par seconde*

Vous pouvez donc faire très facilement des requêtes du genre :

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

Si vous êtes intéressés sur la façon d'implémenter de tels opérateurs, vous
pouvez regarder [l'implémentation de
PoWA](https://github.com/powa-team/powa-archivist/commit/203ed02a5205ad41ce0854bf0580779d7fb6193b#diff-efeed95efc180d43a149361145c2f082R1079).

### Conclusion

Vous connaissez maintenant les bases pour éviter le surcoût de stockage par
ligne.  En fonction de vos besoins et de la spécificité de vos données, vous
devriez pouvoir trouver un moyen d'aggréger vos données, en ajoutant
potentiellement quelques colonnes supplémentaires, afin de conserver de bonnes
performances et économiser de l'espace disque.

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
