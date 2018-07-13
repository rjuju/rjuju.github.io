---
layout: post
title: "Diagnostique de lenteurs inattendues"
modified:
categories: postgresqlfr
excerpt:
tags: [postgresql, performance]
lang: fr
image:
  feature:
date: 2018-07-11T13:04:29+02:00
---

Cet article de blog est le résumé d'un problème rencontré en production que
j'ai eu à diagnostiquer il y a quelques mois avec des gens d'
[Oslandia](https://oslandia.com/en/home-en/), et puisqu'il s'agit d'un problème
pour le moins inhabituel j'ai décidé de le partager avec la méthodologie que
j'ai utilisée, au cas où cela puisse aider d'autres personnes qui
rencontreraient le même type de problème.  C'est également une bonne occasion
de rappeler que mettre à jour PostgreSQL vers une nouvelle version est une
bonne pratique.

### Le problème

Le problème de performance initialement rapporté contenait suffisamment
d'informations pour savoir qu'il s'agissait d'un problème étrange.

Le serveur utilise un PostgreSQL 9.3.5.  Oui, il y a plusieurs versions mineures
de retard, et bien évidémment bon nombre de versions majeures de retard.  La
configuration était également quelque peu inhabituelle.  Les réglages et
dimensionnement physiques les plus importants sont :

    Serveur
        CPU: 40 cœurs, 80 avec l'hyperthreading activé
        RAM: 128 Go
    PostgreSQL:
        shared_buffers: 16 Go
        max_connections: 1500

La valeur élevée pour le `shared_buffers`, surtout puisqu'il s'agit d'une
versions de PostgreSQL plutôt ancienne, est une bonne piste d'investigation.
Le `max_connections` est également assez haut, mais malheureusement l'éditeur
logiciel mentionne qu'il ne supporte pas de pooler de connexion.  Ainsi, la
plupart des connexions sont inactives.  Ce n'est pas idéal car cela implique un
surcoût  pour acquérir un `snapshot`, mais il y a suffisamment de cœurs de processeur pour gérer un grand nombre de connexions.

Le problème principale était que régulièrement, les même requêtes pouvaient
être extrêmement lentes.  Ce simple exemple de reqête était fourni :

{% highlight sql %}
EXPLAIN ANALYZE SELECT count(*) FROM pg_stat_activity ;

-- Quand le problème survient
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

-- Temps de traitement normal
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

Ainsi, bien que le « bon » temps de traitement est un petit peu lent (bien
qu'il y ait 1500 connections), le « mauvais » temps de traitement est plus de
**100 fois plus lent**, pour une requête tout ce qu'il y a de plus simple.

Un autre exemple de requête applicative très simple était fourni, mais avec un
peu plus d'informations.  Voici une versino anonymisée :

{% highlight sql %}
EXPLAIN (ANALYZE, BUFFERS) SELECT une_colonne
FROM une_table
WHERE une_colonne_indexee = 'valeur' AND upper(autre_colonne) = 'autre_value'
LIMIT 1 ;

"Limit  (actual time=7620.756..7620.756 rows=0 loops=1)"
"  Buffers: shared hit=43554"
"  ->  Index Scan using idx_some_table_some_col on une_table  (actual time=7620.754..7620.754 rows=0 loops=1)"
"        Index Cond: ((some_indexed_cold)::text = 'valeur'::text)"
"        Filter: (upper((autre_colonne)::text) = 'autre_value'::text)"
"        Rows Removed by Filter: 17534"
"        Buffers: shared hit=43554"
"Total runtime: 7620.829 ms"

"Limit  (actual time=899.607..899.607 rows=0 loops=1)"
"  Buffers: shared hit=43555"
"  ->  Index Scan using idx_some_table_some_col on une_table  (actual time=899.605..899.605 rows=0 loops=1)"
"        Index Cond: ((some_indexed_cold)::text = 'valeur'::text)"
"        Filter: (upper((autre_colonne)::text) = 'autre_value'::text)"
"        Rows Removed by Filter: 17534"
"        Buffers: shared hit=43555"
"Total runtime: 899.652 ms"
{% endhighlight %}

Il y avait également beaucoup de données de supervision disponibles sur le
système d'exploitation, montrant que les disques, les processeurs et la
mémoire vive avaient toujours des ressources disponibles, et il n'y avait aucun
message intéressant dans la sortie de `dmesg` ou aucune autre trace système.

### Que savons-nous?

Pour la première requête, nous voyons que le parcours d'index interne augmente
de **0.001ms** à **0.6ms**:

{% highlight none %}
->  Index Only Scan using idx on pg_database (actual time=0.001..0.001 rows=1 loops=1088)

->  Index Only Scan using idx on pg_database (actual time=0.610..0.611 rows=1 loops=1088)
{% endhighlight %}

Avec un `shared_buffers` particuli_rement haut et une version de PostgreSQL
ancienne, il est fréquent que des problèmes de lenteur surviennent si la taille
du jeu de données est plus important que le `shared_buffers`, du fait de
l'algorithme dit de « **clocksweep** » utilisé pour sortir les entrées du
`shared_buffers`.

Cependant, la seconde requête montre que le même problème survient alors que
tous les blocs se trouvent dans le `shared_buffers`.  Cela ne peut donc pas
être un problème d'éviction de buffer dû à une valeur de `shared_buffers` trop
élevée, ou un problème de latence sur le disque.

Bien que des paramètres de configuration de PostgreSQL puissent être améliorés,
aucun de ceux-ci ne peuvent expliquer ce comportement en particulier.  Il
serait tout à fait possible que la modification de ces paramètres corrige le
problème, mais il faut plus d'informations pour comprendre ce qui se passe
exactement et éviter tout problème de performance à l'avenir.

### Une idée?

Puisque les explications les plus simples ont déjà été écartées, il faut penser
à des causes de plus bas niveau.

Si vous avez suivi les améliorations dans les dernières versions de PostgreSQL,
vous devriez avoir noté un bon nombre d'optimisations concernant la scalabilité
et le verrouillage.  Si vous voulez plus de détails sur ces sujets, il y a de
nombreux articles de blogs, par exemple [ce très bon
article](http://amitkapila16.blogspot.tw/2015/01/read-scalability-in-postgresql-95.html).

Du côté du du noyau Linux, étant donné le grand nombre de connexions cela peut
ếgalement être, et c'est certainement l'explication la plus probable, dû à
une saturation du
[TLB](https://en.wikipedia.org/wiki/Translation_lookaside_buffer).

Dans tous les cas, pour pouvoir confirmer une théorie il faut utiliser des
outils beaucoup plus pointus.

### Analyse poussée: saturation du TLB

Sans aller trop dans le détail, il faut savoir que chaque processus a une zone
de mémoire utilisée par le noyau pour stocker les « [page tables
entries](https://en.wikipedia.org/wiki/Page_table#PTE) », ou `PTE`,
c'est-à-dire les translations des adresses virtuelles utilisées par le
processus et la vrai adresse physique en RAM.  Cette zone n'est normalement pas
très volumineuse, car un processus n'accès généralement pas à des dizaines de
giga-octets de données en RAM.  Mais puisque PostgreSQL repose sur une
architecture où chaque connexion est un processus dédié qui accède à un gros
segment de mémoire partagée, chaque processus devra avoir une translation
d'adresse pour chaque zone de 4 Ko (la taille par défaut d'une page) du
`shared_buffers` qu'il aura accédé.  Il est donc possible d'avoir une grande
quantité de mémoire utilisée pour la `PTE`, et même d'avoir au total des
translations d'adresse pour adresser bien plus que la quantité total de mémoire
physique disponible sur la machine.

Vous pouvez connaître la taille de la `PTE` au niveau du système d'exploitation
en consultant l'entrée **VmPTE** dans le statut du processus.  Vous pouvez
également vérifier l'entrée **RssShmem** pour savoir pour combien de pages en
mémoire partagée il existe des translations.  Par exemple :

{% highlight bash %}
egrep "(VmPTE|RssShmem)" /proc/${PID}/status
RssShmem:	     340 kB
VmPTE:	     140 kB
{% endhighlight %}

Ce processus n'a pas accédé à de nombreux buffers, sa PTE est donc petite.  If
nous essayons avec un processus qui a accédé à chacun des buffers d'un
shared\hbuffers de 8 Go :

{% highlight bash %}
egrep "(VmPTE|RssShmem)" /proc/${PID}/status
RssShmem:	 8561116 kB
VmPTE:	   16880 kB
{% endhighlight %}

Il y a donc **16 Mo** utilisés pour la PTE !  En multipliant ça par le nombre
de connexion, on arrive à plusieurs giga-octets de mémoire utilisée pour la
PTE.  Bien évidemment, cela ne tiendra pas dans le `TLB`.  Par conséquent, les
processus auront de nombreux « échec de translation » (TLB miss) quand ils
essaieront d'accéder à une page en mémoire, ce qui augmentera la latence de
manière considérable.

Sur le système qui rencontrait ces problèmes de performance, avec **16 Go** de
shared_buffers et **1500** connexions persistente, la mémoire totale utilisée
pour les PTE combinées était d'environ **45 Go** !  Une approximation peut être
faîte avec le script suivant:

{% highlight bash %}
for p in $(pgrep postgres); do grep "VmPTE:" /proc/$p/status; done | awk '{pte += $2} END {print pte / 1024 / 1024}'
{% endhighlight %}

**NOTE:** Cet exemple calculera la mémoire utilisée pour la PTE de tous les
processus postgres.  Si vous avez de plusieurs instances sur la même machine et
que vous voulez connaître l'utilisation par instance, vous devez adapter cette
commande pour ne prendre en compte que les processus dont le ppid est le pid du
postmaster de l'instance voulue.
{: .notice}

C'est évidemment la cause des problèmes rencontrés.  Mais pour en être sûr,
regardons ce que `perf` nous remonte lorsque les problèmes de performance
surviennent, et quand ce n'est pas le cas.

Voici les fonctions les plus consommatrices (consommant plus de 2% de CPU)
remontées par perf lorsque tout va bien :

{% highlight none %}
# Children      Self  Command          Symbol
# ........  ........  ...............  ..................
     4.26%     4.10%  init             [k] intel_idle
     4.22%     2.22%  postgres         [.] SearchCatCache
{% endhighlight %}

Rien de vraiment bien intéressant, le système n'est pas vraiment saturé.
Maintenant, quand le problème survient :

{% highlight none %}
# Children      Self  Command          Symbol
# ........  ........  ...............  ....................
     8.96%     8.64%  postgres         [.] s_lock
     4.50%     4.44%  cat              [k] smaps_pte_entry
     2.51%     2.51%  init             [k] poll_idle
     2.34%     2.28%  postgres         [k] compaction_alloc
     2.03%     2.03%  postgres         [k] _spin_lock
{% endhighlight %}

Nous pouvons voir `s_lock`, la fonction de PostgreSQL qui attend sur un
[spinlock](https://fr.wikipedia.org/wiki/Spinlock) consommant preque 9% du
temps processeur.  Mais il s'agit de PostgreSQL 9.3, et les ligthweight locks
(des verrous internes transitoires) étaient encore implémentés à l'aide de spin
lock ([ils sont maintenant implémentés à l'aide d'opérations
atomiques](https://github.com/postgres/postgres/commit/ab5194e6f617a9a9e7)).
Si nous regardons un peu plus en détails les appeks à `s_lock` :

{% highlight none %}
     8.96%     8.64%  postgres         [.] s_lock
                   |
                   ---s_lock
                      |
                      |--83.49%-- LWLockAcquire
[...]
                      |--15.59%-- LWLockRelease
[...]
                      |--0.69%-- 0x6382ee
                      |          0x6399ac
                      |          ReadBufferExtended
[...]
{% endhighlight %}

99% des appels à `s_lock` sont en effet dûs à des lightweight locks.  Cela
indique un ralentissement général et de fortes contentions.  Mais cela n'est que la conséquence du vrai problème, la seconde fonction la plus consommatrice.

Avec presque 5% du temps processeur, `smaps_pte_entry`, une fonction du noyau
effectuant la translation d'addresse pour une entrée, nous montre le problème.
Cette fonction devrait normalement être extrêmement rapide, et ne devrait même
pas apparaître dans un rapport perf !  Cela veut dire que très souvent, quand
un processus veut accéder à une page en mémoire, il doit attendre pour obtenir
sa vraie adresse.  Mais attendre une translation d'adresse veut dire beaucoup
de [bulles (pipeline stalls)](https://en.wikipedia.org/wiki/Pipeline_stall).
Les processeurs ont des pipelines de plus en plus profonds, et ces bulles
ruinent complètement les bénéfices de ce type d'architecture.  Au final, une
bonne proportion du temps est tout simplement gâchée à attendre des adresses.
Ça explique très certainement les ralentissements extrêmes, ainsi que le manque
de compteurs de plus haut niveau permettant de les expliquer.

### La solution

Plusieurs solutions sont possibles pour résoudre ce problème.

La solution habituelle est de [demande à PostgreSQL d'allouer le
`shared_buffers` dans des huge
pages](https://docs.postgresql.fr/current/kernel-resources.html#linux-huge-pages).
En effet, avec des pages de 2 Mo plutôt que 4 ko, la mémoire utilisée pour la
PTE serait automatiquement diminuée d'un facteur 512.  Cela serait un énorme
gain, et extrêment facile à mettre en place.  Malheureusement, cela n'est
possible qu'à partir de la version 9.4, mais mettre à jour la version majeure
de PostgreSQL n'était pas possible puisque l'éditeur ne supporte pas une
version supérieure à la version 9.3.

Un autre moyen de réduire la taille de la PTE est de réduire le nombre de
connexion, qui ici est assez haut, ce qui aurait probablement d'autres effets
positifs sur les performances.  Encore une fois, ce n'était malheureusement pas
possible puisque l'éditeur affirme ne pas supporter les poolers de connexion et
que le client a besoin de pouvoir gérer un grand nombre de connexions.

Ainsi, la seule solution restante était donc de réduire la taille du
shared\_buffers.  Après quelques essais, la plus haute valeur qui pouvaient
être utilisée sans que les ralentissements extrêmes ne surviennent était de
**4 Go**.  Heureusement, PostgreSQL était capable de conserver des performances
assez bonnes avec cette taille de cache dédié.

Si des des éditeurs logiciels lisent cette article, il faut comprendre que si
on vous demande la compatibilité avec des versions plus récentes de PostgreSQL,
ou avec des poolers de connexion, il y a de très bonnes raisons à cela.  Il y a
généralement très peu de changements de comportement avec les nouvelles
versions, et elles sont toutes documentées !
