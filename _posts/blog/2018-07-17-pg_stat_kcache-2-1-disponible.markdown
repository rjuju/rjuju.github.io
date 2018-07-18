---
layout: post
title: "pg_stat_kcache 2.1 disponible"
modified:
categories: postgresqlfr
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: fr
image:
  feature:
date: 2018-07-17T19:34:13+02:00
---

Une nouvelle version de
[pg_stat_kcache](https://github.com/powa-team/pg_stat_kcache/) est disponible,
ajoutant la compatibilité avec Windows et d'autres plateformes, ainsi que
l'ajout de nouveaux compteurs.

### Nouveautés

La version 2.1 de [pg_stat_kcache](https://github.com/powa-team/pg_stat_kcache/)
vient d'être publiée.

Les deux nouvelles fonctionnalités principales sont:

* compatibilité avec les plateformes ne disposant pas nativement de la fonction
  `getrusage()` (comme Windows) ;
* plus de champs de la fonction `getrusage()` sont exposés.

Comme je l'expliquais dans [a previous article]({% post_url
blog/2015-03-04-pg_stat_kcache-2-0 %}), cette extension est un wrapper sur
[getrusage](http://man7.org/linux/man-pages/man2/getrusage.2.html), qui
accumule des compteurs de performance par requête normalisée.  Cela donnait
déjà de précieuses informations qui permettaient aux DBA d'identifier des
requêtes coûteuse en temps processeur par exemple, ou de calculer un vrai
hit-ratio.

Cependant, cela n'était disponible que sur les plateforme disposant nativement
de la fonction `getrusage`, donc Windows and quelques autres platformes
n'étaient pas supportées.  Heureusement, PostgreSQL permet un [support basique
de `getrusage()`](https://github.com/postgres/postgres/blob/master/src/port/getrusage.c)
sur ces plateformes.  Cette infrastructure a été utilisée dans la version 2.1.0
de pg\_stat\_kcache, ce qui veut dire que vous pouvez maintenant utiliser cette
extension sur Windows et toutes les autres plateformes qui n'étaient auparavant
pas supportées.  Comme il s'agit d'un support limité, seule le temps processeur
utilisateur et système sont supportés, les autres champs seront toujours NULL.

Cette nouvelle version expose également tous les autres champs de `getrusage()`
ayant un sens dans le cadre d'une accumulation par requête :
accumulated per query:

* soft page faults ;
* hard page faults ;
* swaps ;
* messages IPC envoyés et reçus :
* signaux reçus ;
* context switch volontaires et involontaires.

Un autre changement est de détecter automatiquement la précision du chronomètre
système.  Sans celas, les requêtes très rapides (plus rapides que la précision
maximale du chronomètre) seraient détectées soit comme n'ayant pas consommé de
temps processeur, soit ayant consommé le temps processeur d'autres requêtes
très rapides.  Pour les requêtes durant moins que 3 fois la précision du
chronomètre système, où l'imprécision est importante, pg\_stat\_kcache
utilisera à la place la durée d'exécution de la requête comme temps
d'utilisation processeur utilisateur et gardera à 0 le temps d'utilisation
processeur système.

### Un exemple rapide

En fonction de votre plateforme, certains des nouveaux compteurs ne sont pas
maintenus.  Sur GNU/Linux par exemple, les swaps, messages IPC et signeux ne
sont malheureusement pas maintenus, mais ceux qui le sont restent tout à fait
intéressants.  Par exemple, comparons les `context switches` si nous effectuons
le même nombre de transactions, mais avec 2 et 80 connexions concurrentes sur
une machine disposant de 4 cœeurs :

{% highlight bash %}
psql -c "SELECT pg_stat_kcache_reset()"
pgbench -c 80 -j 80 -S -n pgbench -t 100
[...]
number of transactions actually processed: 8000/8000
latency average = 8.782 ms
tps = 9109.846256 (including connections establishing)
tps = 9850.666577 (excluding connections establishing)

psql -c "SELECT user_time, system_time, minflts, majflts, nvcsws, nivcsws FROM pg_stat_kcache WHERE datname = 'pgbench'"
     user_time     |    system_time     | minflts | majflts | nvcsws | nivcsws
-------------------+--------------------+---------+---------+--------+---------
 0.431648000000005 | 0.0638690000000001 |   24353 |       0 |     91 |     282
(1 row)

psql -c "SELECT pg_stat_kcache_reset()"
pgbench -c 2 -j 2 -S -n pgbench -t 8000
[...]
number of transactions actually processed: 8000/8000
latency average = 0.198 ms
tps = 10119.638426 (including connections establishing)
tps = 10188.313645 (excluding connections establishing)

psql -c "SELECT user_time, system_time, minflts, majflts, nvcsws, nivcsws FROM pg_stat_kcache WHERE datname = 'pgbench'"
     user_time     | system_time | minflts | majflts | nvcsws | nivcsws 
-------------------+-------------+---------+---------+--------+---------
 0.224338999999999 |    0.023669 |    5983 |       0 |      0 |       8
(1 row)
{% endhighlight %}

Sans surprise, utiliser 80 connexions concurrentes sur un ordinateur portable
n'ayant que 4 cœeurs n'est pas la manière la plus efficaces de traiter 8000
transactions.  La latence est **44 fois** plus lentes avec 80 connexions plutôt
que 2.  Au niveau du système d'exploitation, on peut voir qu'avec seulement 2
connexions concurrentes, nous n'avons que **8 context switches involontaires**
sur la totalités des requêtes de la base **pgbench**, alors qu'il y en a eu
**282, soit 35 fois plus** avec 80 connexions concurrentes.

Ces nouvelles métriques donnent de nombreuses nouvelles informations sur ce
qu'il se passe au niveau du système d'exploitation, avec une granularité à la
requête normalisée, ce qui pourra faciliter le diagnostique de problèmes de
performances.  Combiné avec [PoWA](https://powa.readthedocs.io/), vous pourrez
même identifier à quel moment n'importe laquelle de ces métriques a un
comportement différent !
