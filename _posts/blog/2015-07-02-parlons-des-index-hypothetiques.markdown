---
layout: post
title: "Parlons des index hypothétiques"
modified:
categories: postgresqlfr
excerpt:
tags: [PoWA, performance, tuning]
image:
  feature:
date: 2015-07-02T11:08:03+01:00
---

Après avoir attendu tellement de temps pour cette fonctionnalité,
[HypoPG](https://github.com/dalibo/hypopg) ajoute le support des index
hypothétiques dans PostgreSQl, sous la forme d'une extension.

### Introduction

Cela fait maintenant quelques temps que la deuxième version de
[PoWA](https://dalibo.github.io/powa) a été annoncée. Une des nouvelles
fonctionnalités de cette version est l'extension
[pg\_qualstats](https://github.com/dalibo/pg_qualstats), écrite par
[Ronan Dunklau](https://rdunklau.github.io).

Grâce à cette extension, il est maintenant possible de collecter des
statistiques en temps réel afin de détecter des index manquants, et bien plus
encore (si cette extension vous intéresse, je vous conseille de lire
[l'article de Ronan sur pg\_qualstats, en anglais](http://rdunklau.github.io/postgresql/powa/pg_qualstats/2015/02/02/pg_qualstats_part1/)).
De plus, si vous l'utilisez avec PoWA, vous aurez une interface qui vous
permettra de trouver les requêtes les plus coûteuses, et suggèrera des index
manquants si c'est le cas.

Ce sont des fonctionnalités vraiment intéressantes, mais maintenant de
nombreuses personnes posent cette question toute naturelle : **Ok, PoWA me dit
qu'il faut que je créé cet index, maix au final est-ce que PostgreSQL
l'utilisera ?**.  C'est une très bonne question, car en fonction de nombreux
paramètres de configuration (entre autres), PostgreSQL pourrait choisir de
simplement ignorer votre index fraîchement créé. Et si vous avez du attendre
plusieurs heures pour sa construction, ça serait une surprise plutôt
déplaisante.

### Index Hypothétiques

Bien evidemment, la réponse à cette question est **le support des index
hypothétiques**. Il ne s'agit vraiment pas d'une nouvelle idée, de nombreux
moteurs de bases de données les supportent déjà.

Il y a d'ailleurs déjà eu de précédents travaux sur le sujet il y a quelques
années, dont les résultats ont été présentés au
[pgCon 2010](http://www.pgcon.org/2010/schedule/events/233.en.html). Ces
travaux allaient beaucoup plus loin que le support des index hypothétiques,
mais il s'agissait d'un travail de recherche, ce qui signifie que les
fonctionnalités qui avaient été développées n'ont jamais vues le jour dans la
version officielle de PostgreSQL. Tout cet excellent travail est
malheureusement uniquement disponible sous la forme de fork de quelques
versions spécifiques de PostgreSQL, la plus récentes étant la 9.0.1.

### Une implémentation plus légère : HypoPG

J'ai utilisé une approche différente pour implémenter les index hypothétiques
avec HypoPG.

  * Pour commencer, cela doit pouvoir s'ajouter sur une version standard de
PostgreSQL. C'est disponible en tant qu'extension et peut être utilisé (pour le
moment) sur n'importe quelle version de PostgreSQL en version 9.2 ou plus ;
  * Cela doit être le moins intrusif possible. C'est utilisable dès que
l'extension a été créée, sans avoir besoin de redémarrer. De plus, chaque
processus client dispose de son propre ensemble d'index hypothétiques.
Concrètement, si vous ajoutez un index hypothétiques, cela ne perturbera
absolument pas les autres connexions. De plus, les index hypothétiques
sont stockés en mémoire, donc ajouter et supprimer un grand nombre d'index
hypothétiques ne fragmentera pas le catalogue système.

La seule restriction pour implémenter cette fonctionnalité sous la forme d'une
extension est qu'il n'est pas possible de modifier la syntaxe sans modifier
le code source de PostgreSQL. Donc tout doit être géré dans des procédures
stockées, et le comportement des fonctionnalités existantes, comme la commande
EXPLAIN, doit être modifié. On verra cela en détail juste après.

### Fonctionnalités

Pour le moment, les fonctions suivantes sont disponibles :

  * **hypopg()**: retourne la liste des index hypothétiques (dans un format
similaire à pg\_index).
  * **hypopg\_add\_index(schema, table, attribute, access\_method)**: créé un
index hypothétique sur une seule colonne.
  * **hypopg\_create\_index(query)**: créé un index hypothétique en utilisant
un ordre standard CREATE INDEX.
  * **hypopg\_drop\_index(oid)**: supprime l'index hypothétique spécifié.
  * **hypopg\_list\_indexes()**: retourne une courte version lisible de la liste
  * des index hypothétiques.
  * **hypopg\_relation\_size(oid)**: retourne la taille estimée d'un index
hypothétique.
  * **hypopg\_reset()**: supprime tous les index hypothétiques.

Si des index hypothétiques existent pour des tables utilisées dans une commande
EXPLAIN (sans ANALYZE), ils seront automatiquement ajoutés à la liste des vrais
index. PostgreSQL choisira alors s'il les utilise ou non.

### Utilisation

Installer HypoPG est plutôt simple. En partant du principe que vous avez
téléchargé et extrait une archive tar dans le répertoire hypopg-0.0.1, que vous
utilisez une version packagée de PostgreSQL et que vous disposez des paquets
-dev :

{% highlight bash %}
$ cd hypopg-0.0.1
$ make
$ sudo make install
{% endhighlight %}

HypoPG devrait alors être disponible :

{% highlight sql %}
rjuju=# CREATE EXTENSION hypopg ;
CREATE EXTENSION
{% endhighlight %}

Voyons quelques tests simplistes. D'abord, créons une petite table :

{% highlight sql %}
rjuju=# CREATE TABLE testable AS SELECT id, 'line ' || id val
rjuju=# FROM generate_series(1,1000000) id;

SELECT 100000
rjuju=# ANALYZE testable ;
ANALYZE
{% endhighlight %}

Ensuite, voyons un plan d'exécution qui pourrait bénéficier d'un index qui n'est
pas présent :

{% highlight sql %}
rjuju=# EXPLAIN SELECT * FROM testable WHERE id < 1000 ;
                          QUERY PLAN
---------------------------------------------------------------
 Seq Scan on testable  (cost=0.00..17906.00 rows=916 width=15)
   Filter: (id < 1000)
(2 rows)

{% endhighlight %}

Sans surprise, un parcours séquentiel est le seul moyen de répondre à cette
requête. Maintenant, essayons d'ajouter un index hypothétique, et refaisons un
EXPLAIN :

{% highlight sql %}
rjuju=# SELECT hypopg_create_index('CREATE INDEX ON testable (id)');
 hypopg_create_index
---------------------
 t
(1 row)

Time: 0,753 ms

rjuju=# EXPLAIN SELECT * FROM testable WHERE id < 1000 ;
                                          QUERY PLAN
-----------------------------------------------------------------------------------------------
 Index Scan using <41079>btree_testable_id on testable  (cost=0.30..28.33 rows=916 width=15)
   Index Cond: (id < 1000)
(2 rows)
{% endhighlight %}

Oui ! Notre index hypothétique est utilisé. On remarque aussi que le temps de
création de l'index hypothétique est d'environ 1ms, ce qui est bien loin du
temps qu'aurait pris la création de cet index.

Et bien entendu, cet index hypothétique n'est pas utilisé dans un
EXPLAIN ANALYZE :

{% highlight sql %}
rjuju=# EXPLAIN ANALYZE SELECT * FROM testable WHERE id < 1000 ;
                                                 QUERY PLAN
-------------------------------------------------------------------------------------------------------------
 Seq Scan on testable  (cost=0.00..17906.00 rows=916 width=15) (actual time=0.076..234.218 rows=999 loops=1)
   Filter: (id < 1000)
   Rows Removed by Filter: 999001
 Planning time: 0.083 ms
 Execution time: 234.377 ms
(5 rows)
{% endhighlight %}

Maintenant essayons d'aller un peu plus loin :

{% highlight sql %}
rjuju=# EXPLAIN SELECT * FROM testable
rjuju=# WHERE id < 1000 AND val LIKE 'line 100000%';

                                         QUERY PLAN
---------------------------------------------------------------------------------------------
 Index Scan using <41079>btree_testable_id on testable  (cost=0.30..30.62 rows=1 width=15)
   Index Cond: (id < 1000)
   Filter: (val ~~ 'line 100000%'::text)
(3 rows)
{% endhighlight %}

Notre index hypothétique est toujours utilisé, mais un index sur **id** et
**val** devrait aider cette requête. De plus, comme il y a un joker sur le côté
droit du motif de recherche du LIKE, la classe d'opérateur
text\_pattern\_ops est requise. Vérifions ça :


{% highlight sql %}
rjuju=# SELECT hypopg_create_index('CREATE INDEX ON testable (id, val text_pattern_ops)');
 hypopg_create_index
---------------------
 t
(1 row)

Time: 1,194 ms

rjuju=# EXPLAIN SELECT * FROM testable
rjuju=# WHERE id < 1000 AND val LIKE 'line 100000%';
                                              QUERY PLAN
------------------------------------------------------------------------------------------------------
 Index Only Scan using <41080>btree_testable_id_val on testable on testable  (cost=0.30..26.76 rows=1 width=15)
   Index Cond: ((id < 1000) AND (val ~>=~ 'line 100000'::text) AND (val ~<~ 'line 100001'::text))
   Filter: (val ~~ 'line 100000%'::text)

(3 rows)

{% endhighlight %}

Et oui, PostgreSQL décide d'utiliser notre nouvel index !

### Estimation de la taille d'index

Il y a pour le moment une estimation rapide de la taille d'index, qui peut nous
donner un indice sur la taille que ferait un vrai index.

Vérifions la taille estimée de nos deux index hypothétiques :

{% highlight sql %}
rjuju=# SELECT indexname,pg_size_pretty(hypopg_relation_size(indexrelid))
rjuju=# FROM hypopg();
           indexname           | pg_size_pretty 
-------------------------------+----------------
 <41080>btree_testable_id     | 25 MB
 <41079>btree_testable_id_val | 49 MB
(2 rows)

{% endhighlight %}

Maintenant, créons les vrais index, et comparons l'espace occupé :

{% highlight sql %}
rjuju=# CREATE INDEX ON testable (id);
CREATE INDEX
Time: 1756,001 ms

rjuju=# CREATE INDEX ON testable (id, val text_pattern_ops);
CREATE INDEX
Time: 2179,185 ms

rjuju=# SELECT relname,pg_size_pretty(pg_relation_size(oid))
rjuju=# FROM pg_class WHERE relkind = 'i' AND relname LIKE '%testable%';
       relname       | pg_size_pretty 
---------------------+----------------
 testable_id_idx     | 21 MB
 testable_id_val_idx | 30 MB
{% endhighlight %}

La taille estimée est un peu plus haute que la taille réelle. C'est volontaire.
En effet, si la taille estimée était moindre que celle d'un index existant,
PostgreSQL préférerait utiliser l'index hypothétique plutôt que le vrai index,
ce qui n'est absolument pas intéressant. De plus, pour simuler un index
fragmenté (ce qui est vraiment très fréquent sur de vrais index), un taux de
fragmentation fixe de 20% est ajoutée. Cependant, cette estimation pourrait être
largement améliorée.

### Limitations

Cette version 0.0.1 d'HypoPG est un travail en cours, et il reste encore
beaucoup de travail à accomplir.

Voilà les principales limitations (du moins qui me viennent à l'esprit) :

  * seuls les index hypothétiques de type btree sont gérés ;
  * pas d'index hypothétiques sur des expressions ;
  * pas d'index hypothétiques sur des prédicats ;
  * il n'est pas possible de spécifier le tablespace ;
  * l'estimation de la taille de l'index pourrait être améliorée, et il n'est
pas possible de changer le pourcentage de fragmentation.

Cependant, cette version peut déjà être utile dans de nombreux contextes.

### Et pour la suite ?

Maintenant, la prochaine étape est d'implémenter le support d'HypoPG dans
[PoWA](https://dalibo.github.io/powa/), pour aider les DBA à décider s'ils
devraient ou non créer les index suggérés, et supprimer les limitations
actuelles.

Si vous voulez essayer HypoPG, le dépôt est disponible ici :
[github.com/dalibo/hypopg](https://github.com/dalibo/hypopg).

À très bientôt pour la suite !
