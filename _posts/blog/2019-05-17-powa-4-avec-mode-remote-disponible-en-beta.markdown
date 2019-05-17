---
layout: post
title: "PoWA 4 apporte un mode remote, disponible en beta !"
modified:
categories: postgresqlfr
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: fr
image:
  feature:
date: 2019-05-17T13:04:17+02:00
---

[PoWA 4](http://powa.readthedocs.io/) est disponible en beta.

### Nouveau mode remote !

Le [nouveau mode remote](https://powa.readthedocs.io/en/latest/remote_setup.html)
est la plus grosse fonctionnalité ajoutée dans PoWA 4, bien qu'il y ait eu
d'autres améliorations.

Je vais décrire ici ce que ce nouveau mode implique ainsi que ce qui a changé
sur l'[UI](https://powa.readthedocs.io/en/latest/components/powa-web/index.html).

Si de plus amples détails sur le reste des changements apportés dans PoWA 4
vous intéresse, je publierai bientôt d'autres articles sur le sujet.

Pour les plus pressés, n'hésitez pas à aller directement sur la [démo v4 de
PoWA](https://dev-powa.anayrat.info/), très gentiment hébergée par [Adrien
Nayrat](http://blog.anayrat.info/).  Aucun authentification n'est requise,
cliquez simplement sur "Login".

### Pourquoi un mode remote est-il important

Cette fonctionnalité a probablement été la plus fréquemment demandée depuis que
PoWA a été publié, en 2014.  Et c'est pour de bonnes raisons, car un mode local
a quelques inconvénients.

Tout d'abord, voyons comment se présentait l'architecture avec les versions 3
et antérieures.  Imaginons une instance contenant 2 bases de données (db1 et
db2)c), ainsi qu'**une base de données dédiée à PoWA**.  Cette base de donénes
dédies contient à la fois les *extensions statistiques* nécessaires pour
récupérer compteurs de performances actuels ainsi que pour **les stocker**.

<img src="/images/powa_4_local.svg">

Un *[background
worker](https://powa.readthedocs.io/en/latest/components/powa-archivist/configuration.html#background-worker-configuration)*
est démarré par PoWA, qui est responsable d'effectuer des *snapshots* et de les
stocker dans la base powa dédiée à intervalle réguliers.  Ensuite, en utilisant
powa-web, vous pouvez consulter l'activité de n'importe laquelle des bases de
données **locales** en effectuant des requêtes sur les données stockées dans la
base dédié, et potentiellement en se connectant sur l'une des autres bases de
données locales lorsque les données complètes sont nécessaires, par exemple
lorsque l'outil de suggestion d'index est utilisé.

Avec la version 4, l'architecture avec une configuration distante change de
manière significative:

<img src="/images/powa_4_remote.svg">

Vous pouvez voir qu'une base de donnée powa dédiée est toujours nécessaire,
mais **uniquement pour les extensions statistiques**.  Les données sont
maintenant stockées sur une instance différente.  Ensuite, le *[background
worker](https://powa.readthedocs.io/en/latest/components/powa-archivist/configuration.html#background-worker-configuration)*
est remplacé par un **[nouveau daemon
collecteur](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)**,
qui lit les métriques de performance depuis les *serveurs distants*, et les
stocke sur le *serveur repository* dédié.  Powa-web pourra présenter les
données en se connectant sur le *serveur repository*, ainsi que sur les
**serveurs distants** lorsque des données complètes sont nécessaires.

En résumé, avec le nouveau mode distant ajouté dans cette version 4

  - un redémarrage de PostgreSQL n'est plus nécessaire pour installer
    powa-archivist
  - il n'y a plus de surcoût du au fait de stocker et requêter les données sur
    le même serveur PostgreSQL que vos serveurs de productions (il y a toujours
    certaines partie de l'UI qui nécessitent d'effectuer des requêtes sur le
    serveur d'origine, par exemple pour montrer des plans avec EXPLAIN, mais le
    surcoût est négligeable)
  - il est maintenant possible d'utiliser PoWA sur un **serveur en
    hot-standby**

L'UI vous accueillera donc maintenant avec une page initiale afin de choisir
lequel des serveurs stockés sur la base de données cible vous voulez
travailler :
<img src="/images/powa_4_all_servers.png">

La principale raison pour laquelle il a fallu tellement de temps pour apporter
ce mode distant est parce que cela apporte beaucoup de complexité, nécessitant
une réécriture majeure de PoWA.  Nous voulions également ajouter d'abord
d'autres fonctionnalités, comme la **suggestion globale d'index**, avec une
**validation grâce à [hypopg](http://hypopg.readthedocs.io/)** introduit avec
[PoWA 3](https://powa.readthedocs.io/en/latest/releases/v3.0.0.html).


### Changements dans [powa-web](https://powa.readthedocs.io/en/latest/components/powa-web/index.html)

L'*interface graphique* est le composant qui a le plus de changements visibles
dans cette version 4.  Voici les plus changements les plus importants.

##### Compatibilité ave le mode distant

Le changement le plus important est bien évidemment le support pour le [nouveau
mode remote](https://powa.readthedocs.io/en/latest/remote_setup.html).  En
conséquence, la première page affichée est maintenant une page de **sélection
de serveur**, affichant tous les *serveurs distants* enregistrés.  Après avoir
choisi le *serveur distant* voulu (ou le *serveur local* si vous n'utilisez pas
le mode distant), toutes les autres pages seront similaires à celles
disponibles jusqu'à la version 3, mais afficheront les données pour un *serveur
distant* spécifique uniquement, et bien entendu en récupérant les données
depuis la **base de données repository**, avec en plus de nouvelles
informations décrites ci-dessous.

Veuillez notez que puisque les données sont maintenant stockées sur un *serveur
repository* dédié quand le mode remote est utilisé, la majorité de l'UI est
utilisable sans se connecter au *serveur distant* sélectionné.  Toutefois,
powa-web nécessite toujours de pouvoir se connecter sur le *serveur distant*
quand les données originales sont nécessaires (par exemple, pour la suggestion
d'index ou pour montrer des plans avec **EXPLAIN**).  Les [mêmes considérations
et possibilités concernant
l'authentification](https://powa.readthedocs.io/en/latest/security.html#connection-on-remote-servers)
que pour le nouveau [daemon powa-collector
](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
(qui sera décrit dans un prochain article) s'appliquent ici.

##### [pg_track_settings](https://github.com/rjuju/pg_track_settings/) support

Quand cette extension est correctement configurée, un nouveau widget timeline
apparaîtra, placé entre chaque graph et son aperçu, affichant différents types
de changements enregistrés si ceux-ci ont été détectés sur l'intervalle de
temps sélectionné.  Sur les pages par base de données et par requête, la liste
sera également filtrée en fonction de la base de données sélectionnée.

La même timeline sera affichée sur chacun des graphs de chacune des pages, afin
de facilement vérifier si ces changements ont eu un impact visible en utilisant
les différents graphs.

Veuillez noter que les détails des changements sont affichés au survol de la
souris.  Vous pouvez également cliquer sur n'importe lequel des événements de
la timeline pour figer l'affichage, et tracer une ligne verticale sur le graph
associé.

Voici un exemple d'un tel changement de configuration en action :

<img src="/images/pg_track_settings_powa4.png">

Veuillez également noter qu'il est nécessaire d'avoir au minimum la version
2.0.0 de [pg_track_settings](https://github.com/rjuju/pg_track_settings/), et
que l'extension doit être installée **à la fois sur les *serveurs distants*
ainsi que sur le *serveur repository*.**

##### Nouveaux graphs disponibles

Quand
[pg_stat_kcache](https://powa.readthedocs.io/en/latest/components/stats_extensions/pg_stat_kcache.html)
est configuré, ses informations n'étaient auparavant affichées que sur la page
par requête.  Les informations sont maintenant également affichées sur les
pages par serveur et par base, dans deux nouveaux graphs :

  * dans le graph **Block Access**, où les métriques **OS cache** et **disk
    read** remplaceront la métrique **read**
  * dans un nouveau graph **System Resources** (qui est également ajouté dans
    la page *par requête*), montrant les [metrics ajoutées dans pg_stat_kcache
    2.1]({% post_url blog/2018-07-17-pg_stat_kcache-2-1-is-out %})

Voici un example de ce nouveau graph **System Resources** :

<img src="/images/pg_stat_kcache_system_resources_powa4.png">

Il y avait également un graph **Wait Events** (disponible quand [l'extension
pg_wait_sampling](https://powa.readthedocs.io/en/v4/components/stats_extensions/pg_wait_sampling.html)
est configuée) disponible uniquement sur la page par requête.  Ce graph est
maintenant disponible sur les pages par serveur et par base également.

##### Documentation des métriques et liens vers la documentation

Certaines métriques affichées sur l'interface sont assez parlante, mais
certaines autres peuvent être un peu obscures.  Jusqu'à maintenant, il n'y
avait malheureusement aucune documentation pour les métriques.  Le problème est
maintenant réglé, et tous les graphs ont une *icône d'information*, qui
affichent une description des métriques utilisée dans le graph au survol de la
souris.  Certains graphs incluent également un lien vers la [documentation PoWA
de extension
statistiques](https://powa.readthedocs.io/en/latest/components/stats_extensions/index.html)
pour les utilisateurs qui désirent en apprendre plus à leur sujet.

Voici un exemple :

<img src="/images/powa_4_metrics_doc.png">

##### Et des correctifs de bugs divers

Certains problèmes de longues dates ont également été rapportés :

  * la boîte affichée au survol d'un graph montant les valeurs des métriques
    avait une position verticale incorrecte
  * la sélection temporelle en utilisant l'aperçu des graphs ne montrait pas un
    aperçu correct après avoir appliqué la sélection
  * les erreurs lors de la création d'index hypothétiques ou dans certains cas
    leur affichage n'était pas correctement gérés sur plusieurs pages
  * les filtres des tableaux n'était pas réappliqués quand l'intervalle de
    temps sélectionné était changé

Si un de ces problèmes vous a un jour posé problème, vous serez ravi
d'apprendre qu'ils sont maintenant tous corrigés !

### Conclusion

Cette 4ème version de PoWA représente un temps de développement très important,
de nombreuses améliorations sur la documentation et beaucoup de tests.  Nous
somme maintenant assez satisfaits, mais il est possible que nous ayons ratés
certains bugs.  Si vous vous intéressez à ce projet, j'espère que vous
essaierez de tester cette beta, et si besoin n'hésitez pas à [nous remonter un
bug](https://powa.readthedocs.io/en/latest/support.html#support)!
