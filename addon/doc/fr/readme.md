# Module Open AI pour NVDA

Ce module est conçu pour intégrer parfaitement les capacités de l'API Open AI dans votre travail. Que vous souhaitiez créer du texte complet, traduire des passages avec précision, résumer des documents de manière concise, ou même interpréter et décrire du contenu visuel, ce module complémentaire fait tout cela avec facilité.

## Installation

1. Allez sur la page des [versions](https://github.com/aaclause/nvda-OpenAI/releases) pour trouver la dernière version de l'extension.
2. Téléchargez la dernière version à partir du lien fourni.
3. Exécutez le programme d'installation pour ajouter le module complémentaire à votre environnement NVDA.

## Prérequis pour l'utilisation

Pour utiliser toutes les fonctionnalités du module OpenAI pour NVDA, une clé API de OpenAI est requise. Suivez ces étapes pour la configurer :

1. Obtenez une clé API en vous inscrivant à un compte OpenAI à l'adresse suivante : [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Avec la clé API prête, vous avez deux options de configuration :
	- Par le biais de la boîte de dialogue des paramètres de NVDA :
		1. Accédez au menu NVDA et naviguez vers le sous-menu "Préférences".
		2. Ouvrez la boîte de dialogue "Paramètres" et sélectionnez la catégorie "Open AI".
		3. Saisissez votre clé API dans le champ prévu à cet effet et cliquez sur "OK" pour confirmer.
	- En utilisant des variables d'environnement :
		1. Appuyez sur `Windows+Pause` pour ouvrir les Propriétés du système.
		2. Cliquez sur "Paramètres avancés du système" et sélectionnez "Variables d'environnement".
		3. Créez une nouvelle variable sous "Variables utilisateur" :
			1. Cliquez sur "Nouveau".
			2. Entrez `OPENAI_API_KEY` comme nom de la variable et collez votre clé API comme valeur.
		4. Cliquez sur "OK" pour enregistrer vos modifications.

Vous êtes maintenant prêt à explorer les fonctionnalités du module OpenAI pour NVDA !

## Comment utiliser le module

### Le dialogue principal

La majorité des fonctionnalités de l'extension sont facilement accessibles via une boîte de dialogue, qui peut être lancée en appuyant sur `NVDA+G`.  
Vous pouvez également vous rendre dans le sous-menu "Open AI" du menu NVDA et sélectionner l'élément "Dialogue principal…".  
Dans cette boîte de dialogue, vous pourrez :

- Entamer des conversations interactives avec les modèles d'IA pour obtenir de l'aide ou des informations.
- Obtenir des descriptions d'images à partir de fichiers d'images.
- Transcrire du contenu parlé à partir de fichiers audio ou à l'aide d'un microphone.
- Utiliser la fonction de synthèse vocale pour vocaliser le texte écrit dans l'invite.

#### Augmentez votre productivité grâce aux raccourcis

Pour améliorer votre interaction avec l'interface, veuillez prendre note de ce qui suit :

- Les champs multilignes "Système", "Historique" et "Prompt" sont dotés de menus contextuels contenant des commandes qui peuvent être exécutées rapidement à l'aide de raccourcis clavier.
  Ces raccourcis sont actifs lorsque le champ concerné a le focus.
  Par exemple, les touches "j" et "k" permettent de naviguer respectivement vers les messages précédents et suivants lorsque le champ "Historique" est en focus.

- En outre, l'interface comprend des raccourcis clavier qui s'appliquent à l'ensemble de la fenêtre. Par exemple, `CTRL + R` démarre ou arrête un enregistrement.

Tous les raccourcis clavier sont affichés à côté des libellés des éléments correspondants.

#### Au sujet de la case à cocher « Mode conversation »

La case à cocher du mode conversation est conçue pour améliorer votre expérience de la discussion et économiser des jetons de saisie.

Lorsqu'elle est activée (paramètre par défaut), le module complémentaire transmet l'intégralité de l'historique de la conversation au modèle d'IA, ce qui lui permet d'améliorer sa compréhension du contexte et d'obtenir des réponses plus cohérentes. Ce mode complet entraîne une plus grande consommation de jetons de saisie.

À l'inverse, lorsque la case n'est pas cochée, seul le prompt courant est envoyée au modèle d'IA. Sélectionnez ce mode pour poser des questions spécifiques ou obtenir des réponses discrètes, en évitant la compréhension du contexte et en conservant les jetons de saisie lorsque l'historique du dialogue n'est pas nécessaire.

Vous pouvez passer d'un mode à l'autre à tout moment au cours d'une session.

### À propos du champ `Système`

Le champ `Système` est conçu pour affiner le comportement et la personnalité du modèle d'IA afin de répondre à vos attentes spécifiques.

- **Invite par défaut** : Lors de l'installation, le module complémentaire inclut un prompt système par défaut prêt à l'emploi.
- **Personnalisation** : Vous avez la liberté de personnaliser l'invite du système en modifiant le texte directement dans le champ. Le module complémentaire se souviendra du dernier prompt système utilisé et le chargera automatiquement la prochaine fois que vous lancerez la boîte de dialogue. Ce comportement peut être désactivé dans les paramètres.
- **Option de réinitialisation** : Vous souhaitez revenir à la configuration standard ? Utilisez simplement le menu contextuel pour réinitialiser le champ `Système` à sa valeur par défaut sans effort.

Veuillez noter que le prompt système est inclus dans les données d'entrée du modèle d'IA, entraînant ainsi une consommation de jetons correspondante.

### Commandes globales

Ces commandes peuvent être utilisées pour déclencher des actions à partir de n'importe où sur votre ordinateur. Il est possible de les réaffecter à partir de la boîte de dialogue *Gestes de commandes* sous la catégorie *Open AI*.

- `NVDA+e` : Faire une capture d'écran et la décrire.
- `NVDA+o` : Saisissir l'objet de navigateur actuel et le décrire.
- Commandes assignées à aucun geste par défaut :
	- Basculer l'enregistrement du microphone et transcrire l'audio de n'importe où.

## Dépendances incluses

Le module complémentaire est fourni avec les dépendances essentielles suivantes :

- [OpenAI](https://pypi.org/project/openai/) : La bibliothèque Python officielle pour l'API openai.
- [markdown2](https://pypi.org/project/markdown2/): Une implémentation rapide et complète de Markdown en Python.
- [MSS](https://pypi.org/project/mss/) : Un module multi-captures d'écran ultra-rapide multi-plateformes en Python pur utilisant ctypes.
- [Pillow](https://pypi.org/project/Pillow/): Le fork convivial de la bibliothèque Python Imaging Library, utilisée pour le redimensionnement des images.
- [sounddevice](https://pypi.org/project/sounddevice/) : Jouer et enregistrer du son avec Python.
