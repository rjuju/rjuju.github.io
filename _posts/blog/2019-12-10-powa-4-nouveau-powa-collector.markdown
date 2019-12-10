---
layout: post
title: "PoWA 4: Nouveau daemon powa-collector"
modified:
categories: postgresqlfr
excerpt:
tags: [ postgresql, monitoring, PoWA, performance]
lang: fr
image:
  feature:
date: 2019-12-10T19:54:17+01:00
---

Cet article fait partie d'une série d'article sur [la beta de PoWA
4](http://powa.readthedocs.io/), et décrit le nouveau [daemon
powa-collector](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html).

### Nouveau [daemon powa-collector](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)

Ce daemon remplace le précédent *background worker* lorsque le nouveau [mode
remote](https://powa.readthedocs.io/en/latest/remote_setup.html) est utilisé.
Il s'agit d'un simple daemon écrit en python, qui s'occupera de toutes les
étapes nécessaires pour effectuer des *snapshots distants*.  Il est [disponible
sur pypi](https://pypi.org/project/powa-collector/).

Comme je l'ai expliqué dans mon [précédent article introduistant PoWA 4]({%
post_url blog/2019-05-17-powa-4-with-remote-mode-beta-is-available %}), ce
daemon est nécessaire  pour la configuration d'un mode remote, en gardant cette
architecture à l'esprit :

<img src="/images/powa_4_remote.svg">

Sa configuration est très simple.  Il vous suffit tout simplement de renommer
le fichier `powa-collector.conf.sample` fourni, et d'adapter [l'URI de
connexion](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING)
pour décrire comment se connecter sur votre *serveur repository* dédié, et
c'est fini.

Une configuration typique devrait ressembler à :

{% highlight conf %}
{
    "repository": {
        "dsn": "postgresql://powa_user@server_dns:5432/powa",
    },
    "debug": true
}
{% endhighlight %}

La liste des *serveur distants*, leur configuration ainsi que tout le reste qui
est nécessaire pour le bon fonctionnement sera automatiquement récupéré depuis
le *serveur repository* que vous ave déjà configuré.  Une fois démarré, il
démarrera un thread dédié par *serveur distant* déclaré, et maintiendra une
**connexion persistente** sur ce *serveur distant*.  Chaque thread effectuera
un *snapshot distant*, exportant les données sur le *serveur repository* en
utilisant les nouvelles *fonctions sources*.  Chaque thread ouvrira et fermera
une connexion sur le *serveur repository* lors de l'exécution du *snapshot
distant*.

Bien évidemment, ce daemon a besoin de pouvoir se connecter sur tous les
*serveurs distants* déclarés ainsi que le *serveur repository*.  La table
`powa_servers`, qui stocke la liste des *serveurs distants*, a un champ pour
stocker les nom d'utilisateur et mot de passe pour se connecter aux *serveur
distants*.  Stocker un mot de passe en clair dans cette table est une hérésie,
si l'on considère l'aspect sécurité.  Ainsi, comme indiqué dans la
[section sécurité de
PoWA](https://powa.readthedocs.io/en/latest/security.html#connection-on-remote-servers),
vous pouve stocker un mot de passe NULL et [utiliser à la place n'importe
laquelle des autres méthodes d'authentification supportées par la
libpq](https://www.postgresql.org/docs/current/auth-methods.html) (fichier
.pgpass, certificat...).  C'est très fortement recommandé pour toute
installation sérieuse.

La connexion persistente sur le *serveur repository* est utilisée pour
superviser la daemon :

  * pour vérifier  que le daemon est bien démarré
  * pour communiquer au travers de l'UI en utilisant un [protocole simple](https://powa.readthedocs.io/en/latest/components/powa-collector/protocol.html)
    afin d'effectuer des actions diverses (recharger la configuration, vérifier
    le status d'un thread dédié à un *serveur distant*...)

Il est à noter que vous pouvez également demander au daemon de recharger sa
configuration en envoyant un SIGHUP au processus du daemon.  Un rechargement
est nécessaire pour toute modification effectuée sur la liste des serveurs
distants (ajout ou suppression d'un *serveur distant*, ou mise à jour d'un
existant).

Veuillez également noter que, par choix,
[powa-collector](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
n'effectuera pas de *snapshot local*.  Si vous voulez utiliser PoWA pour le
*serveur repository*, il vous faudra activer le *background worker* original.

##### Nouvelle page de configuration

La page de configuration est maintenant modifiée pour donner toutes les
informations nécessaires sur le status du background worker, le [powa-collector
daemon](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
(incluant tous ses threads dédiés) ainsi que la liste des *serveurs distants*
déclarés.  Voici un exemple de cette nouvelle page racine de configuration :

<img src="/images/powa_4_configuration_page.png">

Si le [daemon
powa-collector](https://powa.readthedocs.io/en/latest/components/powa-collector/index.html)
est utilisé, le status de chaque serveur distant sera récupéré en utilisant le
protocole de communication.  Si le collecteur rencontre des erreurs (lors de la
connexion à un *serveur distant*, durant un *snapshot* par exemple), celles-ci
seront également affichées ici.  À noter également que ces erreurs seront
également affichées en haut de chaque page de toutes les pages de l'UI, afin
d'être sûr de ne pas les rater.

De plus, la section configuration a maintenant une hiérarchie, et vous pourrez
voir la liste des extensions ainsi que la configuration actuelle de PostgreSQL
pour le serveur **local** ou **distant** en cliquant sur le serveur de votre
choix!

Il y a également un nouveau bouton **Reload collector** sur le bandeau
d'en-tête qui, comme on pourrait s'y attendre, demandera au collecteur de
recharger sa configuration.  Cela peut être utile si vous avez déclarés de
nouveaux serveurs mais n'ave pas d'accès au serveur sur lequel le collecteur
s'exécute.

### Conclusion

Cette article est le dernier de la séurie concernant la nouvelle version de
PoWA.  Il est toujours en beta, n'hésitez donc pas à le tester, [rapporter
tout bug rencontré](https://powa.readthedocs.io/en/latest/support.html#support)
ou donner tout autre retour!
