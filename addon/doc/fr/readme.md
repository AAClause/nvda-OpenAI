# Extension Open AI pour NVDA

Cette extension est conçue pour intégrer de manière transparente les capacités de l'API Open AI dans votre flux de travail. Que vous cherchiez à rédiger des textes complets, traduire des passages avec précision, résumer concisément des documents, ou même interpréter et décrire du contenu visuel, cette extension le fait aisément.

L'extension prend également en charge l'intégration avec les services Mistral et OpenRouter, grâce à leur format d'API commun.

## Étapes d'installation

1. Allez sur la [page des versions](https://github.com/aaclause/nvda-OpenAI/releases) pour trouver la dernière version de l'extension.
2. Téléchargez la dernière version depuis le lien fourni.
3. Exécutez l'installateur pour ajouter l'extension à votre environnement NVDA.

## Configuration des clés API

Pour utiliser cette extension, vous devez la configurer avec une clé API de votre fournisseur de services sélectionné ([OpenAI](https://platform.openai.com/), [Mistral AI](https://mistral.ai/), et/ou [OpenRouter](https://openrouter.ai/)). Chaque fournisseur propose un processus simple pour l'acquisition et l'intégration de la clé API.

Une fois que vous avez votre clé API, l'étape suivante est de l'intégrer avec l'extension :

- Ouvrez le menu NVDA, allez dans  'Préférences' puis 'Paramètres'. Dans la fenêtre de dialogue 'Paramètres', choisissez la catégorie "Open AI".
- Dans cette catégorie, vous remarquerez un groupe étiqueté 'Clés API' qui contient des boutons nommés d'après les fournisseurs de services pris en charge (p.ex., "Clés API OpenAI...").
- Cliquez sur le bouton pertinent pour votre service. Une boîte de dialogue apparaîtra, vous demandant non seulement votre clé API, mais aussi une clé d'organisation si vous en avez une. Ceci est particulièrement utile pour l'intégration avec les services qui différencient entre les usages personnels et organisationnels.
- Remplissez votre clé API et, le cas échéant, votre clé d'organisation dans les champs respectifs et cliquez sur 'OK' pour enregistrer vos paramètres.

Vous êtes maintenant prêt à explorer les fonctionnalités de l'extension OpenAI NVDA !

## Comment utiliser le module

### Le dialogue principal

La majorité des fonctionnalités de l’extension sont facilement accessibles via une boîte de dialogue, qui peut être lancée en appuyant sur `NVDA+G`.  
Vous pouvez également vous rendre dans le sous-menu "Open AI" du menu NVDA et sélectionner l’élément "Dialogue principal…".  
Dans cette boîte de dialogue, vous pourrez :

- Entamer des conversations interactives avec les modèles d’IA pour obtenir de l’aide ou des informations.
- Obtenir des descriptions d’images à partir de fichiers d’images.
- Transcrire du contenu parlé à partir de fichiers audio ou à l’aide d’un microphone.
- Utiliser la fonction de synthèse vocale pour vocaliser le texte écrit dans l’invite.

#### Augmentez votre productivité grâce aux raccourcis

Pour améliorer votre interaction avec l’interface, veuillez prendre note de ce qui suit :

- Les champs multilignes "Système", "Historique" et "Prompt" sont dotés de menus contextuels contenant des commandes qui peuvent être exécutées rapidement à l’aide de raccourcis clavier.
  Ces raccourcis sont actifs lorsque le champ concerné a le focus.
  Par exemple, les touches "j" et "k" permettent de naviguer respectivement vers les messages précédents et suivants lorsque le champ "Historique" est en focus.

- En outre, l’interface comprend des raccourcis clavier qui s’appliquent à l’ensemble de la fenêtre. Par exemple, `CTRL + R` démarre ou arrête un enregistrement.

Tous les raccourcis clavier sont affichés à côté des libellés des éléments correspondants.

#### Au sujet de la case à cocher « Mode conversation »

La case à cocher du mode conversation est conçue pour améliorer votre expérience de la discussion et économiser des jetons de saisie.

Lorsqu’elle est activée (paramètre par défaut), le module complémentaire transmet l’intégralité de l’historique de la conversation au modèle d’IA, ce qui lui permet d’améliorer sa compréhension du contexte et d’obtenir des réponses plus cohérentes. Ce mode complet entraîne une plus grande consommation de jetons de saisie.

À l’inverse, lorsque la case n’est pas cochée, seul le prompt courant est envoyée au modèle d’IA. Sélectionnez ce mode pour poser des questions spécifiques ou obtenir des réponses discrètes, en évitant la compréhension du contexte et en conservant les jetons de saisie lorsque l’historique du dialogue n’est pas nécessaire.

Vous pouvez passer d’un mode à l’autre à tout moment au cours d’une session.

#### À propos du champ `Système`

Le champ `Système` est conçu pour affiner le comportement et la personnalité du modèle d’IA afin de répondre à vos attentes spécifiques.

- **Invite par défaut** : Lors de l’installation, le module complémentaire inclut un prompt système par défaut prêt à l’emploi.
- **Personnalisation** : Vous avez la liberté de personnaliser l’invite du système en modifiant le texte directement dans le champ. Le module complémentaire se souviendra du dernier prompt système utilisé et le chargera automatiquement la prochaine fois que vous lancerez la boîte de dialogue. Ce comportement peut être désactivé dans les paramètres.
- **Option de réinitialisation** : Vous souhaitez revenir à la configuration standard ? Utilisez simplement le menu contextuel pour réinitialiser le champ `Système` à sa valeur par défaut sans effort.

Veuillez noter que le prompt système est inclus dans les données d’entrée du modèle d’IA, entraînant ainsi une consommation de jetons correspondante.

### Commandes globales

Ces commandes peuvent être utilisées pour déclencher des actions à partir de n’importe où sur votre ordinateur. Il est possible de les réaffecter à partir de la boîte de dialogue *Gestes de commandes* sous la catégorie *Open AI*.

- `NVDA+e` : Faire une capture d’écran et la décrire.
- `NVDA+o` : Saisissir l’objet de navigateur actuel et le décrire.
- Commandes assignées à aucun geste par défaut :
	- Basculer l’enregistrement du microphone et transcrire l’audio de n’importe où.

## Dépendances incluses

Le module complémentaire est fourni avec les dépendances essentielles suivantes :

- [OpenAI](https://pypi.org/project/openai/) : La bibliothèque Python officielle pour l’API openai.
- [markdown2](https://pypi.org/project/markdown2/): Une implémentation rapide et complète de Markdown en Python.
- [MSS](https://pypi.org/project/mss/) : Un module multi-captures d’écran ultra-rapide multi-plateformes en Python pur utilisant ctypes.
- [Pillow](https://pypi.org/project/Pillow/): Le fork convivial de la bibliothèque Python Imaging Library, utilisée pour le redimensionnement des images.
- [sounddevice](https://pypi.org/project/sounddevice/) : Jouer et enregistrer du son avec Python.
