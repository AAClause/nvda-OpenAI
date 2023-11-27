# Module Open AI pour NVDA

Ce module est conçu pour intégrer parfaitement les capacités de l'API Open AI dans votre travail. Que vous souhaitiez créer du texte complet, traduire des passages avec précision, résumer des documents de manière concise, ou même interpréter et décrire du contenu visuel, ce module complémentaire fait tout cela avec facilité.

## Installation

1. Allez sur la page des [versions](https://github.com/aaclause/nvda-OpenAI/releases) pour trouver la dernière version de l'extension.
2. Téléchargez la dernière version à partir du lien fourni.
3. Exécutez le programme d'installation pour ajouter le module complémentaire à votre environnement NVDA.

## Prérequis pour l'utilisation

Pour utiliser toutes les fonctionnalités du module OpenAI pour NVDA, une clé API de OpenAI est requise. Suivez ces étapes pour la configurer :

1. Obtenez une clé API en vous inscrivant à un compte OpenAI à l'adresse suivante : [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Avec la clé API prête, vous avez deux options de configuration :
   - Par le biais de la boîte de dialogue des paramètres de NVDA :
     1. Accédez au menu NVDA et naviguez vers le sous-menu "Préférences".
     2. Ouvrez la boîte de dialogue "Paramètres" et sélectionnez la catégorie "Open AI".
     3. Saisissez votre clé API dans le champ prévu à cet effet et cliquez sur "OK" pour confirmer.
   - En utilisant des variables d'environnement :
     1. Appuyez sur `Windows+Pause` pour ouvrir les Propriétés du système.
     2. Cliquez sur "Paramètres avancés du système" et sélectionnez "Variables d'environnement".
     3. Créez une nouvelle variable sous "Variables utilisateur" :
         1. Cliquez sur "Nouveau".
         2. Entrez `OPENAI_API_KEY` comme nom de la variable et collez votre clé API comme valeur.
     4. Cliquez sur "OK" pour enregistrer vos modifications.

Vous êtes maintenant prêt à explorer les fonctionnalités du module OpenAI pour NVDA !

## Comment utiliser le module

### Accès aux principales fonctionnalités

La fonctionnalité du module est situé dans une boîte de dialogue centrale qui peut être ouverte en utilisant le raccourci `NVDA+g`. Cette boîte de dialogue donne accès à la majorité des fonctionnalités du module complémentaire, ce qui vous permet de :

- Engager une conversation avec le modèle d'IA.
- Obtenir des descriptions d'images à partir de fichiers d'images.
- Transcrire du contenu oral à partir de fichiers audio ou via un microphone.
- Utiliser la fonction Text-To-Tpeech pour vocaliser le texte écrit dans le Prompt.

#### Commandes à partir de la boîte de dialogue principale

Certaines commandes sont disponibles dans la boîte de dialogue principale pour différents éléments.

- Lorsque le champ du prompt est sélectionné :
	- `Ctrl+Entrée` : Soumettre le texte que vous avez entré.
	- `Ctrl+Flèche haut` : Récupérez et placez le prompt le plus récent dans le champ actuel pour relecture ou réutilisation.
- Lorsque le champ de l'historique est sélectionné :
	- `Alt+Flèche droite` : Copier le texte de l'utilisateur dans le prompt.
	- `Alt+Flèche gauche` : Copier la réponse de l'assistant dans le système.
	- `Ctrl+C` : Copier la réponse de l'assistant ou le texte de l'utilisateur en fonction de la position du curseur.
	- `Ctrl+Maj+Flèche haut` : Passer au bloc de texte de l'utilisateur ou de l'assistant au-dessus du bloc actuel.
	- `Ctrl+Maj+Flèche bas` : Passez au bloc de texte de l'utilisateur ou de l'assistant en dessous du bloc actuel.

### Commandes globales

Ces commandes peuvent être utilisées pour déclencher des actions à partir de n'importe où sur votre ordinateur. Il est possible de les réaffecter à partir de la boîte de dialogue *Gestes de commandes* sous la catégorie *Open AI*.

- `NVDA+e` : Faire une capture d'écran et la décrire.
- `NVDA+o` : Saisissir l'objet de navigateur actuel et le décrire.

## Dépendances incluses

Le module complémentaire est fourni avec les dépendances essentielles suivantes :

- [OpenAI](https://pypi.org/project/openai/) : La bibliothèque Python officielle pour l'API openai.
- [MSS](https://pypi.org/project/mss/) : Un module multi-captures d'écran ultra-rapide multi-plateformes en Python pur utilisant ctypes.
- [sounddevice](https://pypi.org/project/sounddevice/) : Jouer et enregistrer du son avec Python.