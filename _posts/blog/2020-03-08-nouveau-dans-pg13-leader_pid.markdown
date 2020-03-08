---
layout: post
title: "Nouveau dans pg13: Colonne leader_pid dans pg_stat_activity"
modified:
categories: postgresqlfr
excerpt:
tags: [postgresql, monitoring, pg13, new_feature]
lang: fr
image:
  feature:
date: 2020-03-08T07:33:26+02:00
---

### Nouvelle colonne leader_pid dans la vue pg_stat_activity

Étonnamment, depuis que les requêtes parallèles ont été ajoutées dans
PostgreSQL 9.6, il était impossible de savoir à quel processus client était lié
un worker parallèle.  Ainsi, comme [Guillaume l'a fait
remarquer](https://twitter.com/g_lelarge/status/1209486212190343168), it makes
il est assez difficile de construire des outils simples permettant
d'échantillonner les événements d'attente liés à tous les processus impliqués
dans une requête.  Une solution simple à ce problème est d'exporter
l'information de `lock group leader` disponible dans le processus client au
niveau SQL :

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

Avec cette modification, il est maintenant très simple de trouver tous les
processus impliqués dans une requête parallèle.  Par exemple :

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

Attention toutefois, comme indiqué dans le message de commit, si la colonne
`leader_pid` à la même valeur que la colonne `pid`, cela ne veut pas forcément
dire que le processus client est actuellement en train d'effectuer une requête
parallèle, car une fois que le champ est positionné il n'est jamais
réinitialisé.  De plus, pour éviter tout surcoût, aucun verrou supplémentaire
n'est maintenu lors de l'affichage de ces données.  Cela veut dire que chaque
ligne est traitée indépendamment.  Ainsi, bien que cela soit fort peu probable,
vous pouvez obtenir des données incohérentes dans certaines circonstances,
comme par exemple un worker paralèlle pointant vers un pid qui est déjà
déconnecté.
