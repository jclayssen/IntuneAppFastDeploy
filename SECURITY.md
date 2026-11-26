# Security Policy

**English** · [Français](#politique-de-sécurité)

IntuneAppFastDeploy lets signed-in administrators publish software to the devices of an organization. A flaw in it can have real consequences, so reports are welcome and taken seriously.

## Supported versions

The project is a **test version** and has a single supported line: the latest image, `ghcr.io/jclayssen/intuneappfastdeploy:latest`, built from the `main` branch. Fixes are published there only; there are no maintenance branches.

| Version | Supported |
| --- | --- |
| `latest` (branch `main`) | Yes |
| Any older image or commit | No — update first: `docker compose pull && docker compose up -d` |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub: **[Report a vulnerability](https://github.com/jclayssen/IntuneAppFastDeploy/security/advisories/new)** (repository **Security** tab → **Report a vulnerability**). Only the maintainer sees the report.

Please include:

- what an attacker can do and under which conditions (signed in or not, network position);
- the steps to reproduce, with the version shown at the bottom of the interface or the image digest;
- relevant parts of your `docker-compose.yml` — **remove your tenant ID, client ID and any secret**;
- logs if useful (`docker compose logs app`), with tenant-specific values removed.

Never include real credentials, tokens or data from your tenant in a report.

## What to expect

This project is maintained by one person, on a volunteer basis, who can be away for a few weeks at a time. There is no guaranteed response time. The aim is to:

- acknowledge a report within **30 days**;
- tell you whether it is accepted, and what is planned, within **60 days**;
- publish a fix in `latest` as soon as it is ready, then a GitHub security advisory crediting you, unless you prefer to stay anonymous.

If the report is declined (not reproducible, out of scope, or expected behaviour), you get the reason. Please keep the details private until a fix is published or 120 days have passed, whichever comes first. A silence of a few weeks means the maintainer is away, not that the report is ignored.

## Scope

In scope:

- the code of this repository and the published Docker image;
- sign-in and session handling, the HTTP API, the handling of downloaded installers;
- the build and publication workflows of this repository.

Out of scope:

- Microsoft Intune, Microsoft Graph and Microsoft Entra ID themselves — report those to [Microsoft](https://msrc.microsoft.com/);
- an installer you chose to publish being malicious: the tool publishes what it is given;
- what a signed-in Intune administrator can do with rights they already have in the Intune portal;
- deployments that ignore the recommendations below.

## How the project is kept secure

- **Dependencies**: exact versions are recorded in a lock file. [Renovate](https://github.com/apps/renovate) opens a pull request for every update, immediately for a known vulnerability. A person reviews and merges each one; nothing is merged automatically.
- **Code scanning**: CodeQL analyses the Python code, the interface and the workflows on every change and every week.
- **Secrets**: GitHub secret scanning and push protection are enabled on the repository.
- **Image**: rebuilt on every change and every Monday, so the base system receives its security patches. It runs as an unprivileged user with all Linux capabilities dropped.
- **No stored secret**: sign-in uses the authorization code flow with PKCE; tokens stay in memory, and every action is made with the rights of the signed-in person.

## Recommendations for your deployment

- Keep the tool on `localhost` or an internal network. Do not expose it to the Internet unless you have to.
- Away from `localhost`, use the HTTPS front door.
- Limit who can sign in (`ALLOWED_USERS`, or *Assignment required* on the enterprise application in Entra ID).
- Update regularly: `docker compose pull && docker compose up -d`.
- Compare the SHA-256 fingerprint shown on the review screen with the one published by the vendor before publishing an installer.

---

# Politique de sécurité

[English](#security-policy) · **Français**

IntuneAppFastDeploy permet à des administrateurs connectés de publier des logiciels sur les postes d'une organisation. Une faille peut avoir des conséquences réelles : les signalements sont les bienvenus et pris au sérieux.

## Versions prises en charge

Le projet est une **version de test** et n'a qu'une seule ligne prise en charge : la dernière image, `ghcr.io/jclayssen/intuneappfastdeploy:latest`, construite à partir de la branche `main`. Les correctifs n'y sont publiés que là ; il n'y a pas de branche de maintenance.

| Version | Prise en charge |
| --- | --- |
| `latest` (branche `main`) | Oui |
| Toute image ou tout commit plus ancien | Non — mettez d'abord à jour : `docker compose pull && docker compose up -d` |

## Signaler une vulnérabilité

**N'ouvrez pas de ticket public pour un problème de sécurité.**

Signalez-le en privé via GitHub : **[Report a vulnerability](https://github.com/jclayssen/IntuneAppFastDeploy/security/advisories/new)** (onglet **Security** du dépôt → **Report a vulnerability**). Seul le mainteneur voit le signalement.

Indiquez :

- ce qu'un attaquant peut faire et dans quelles conditions (connecté ou non, position sur le réseau) ;
- les étapes pour reproduire, avec la version affichée en bas de l'interface ou l'empreinte de l'image ;
- les parties utiles de votre `docker-compose.yml` — **retirez l'ID de tenant, l'ID client et tout secret** ;
- les journaux si nécessaire (`docker compose logs app`), sans les valeurs propres à votre tenant.

N'incluez jamais d'identifiants réels, de jetons ou de données de votre tenant dans un signalement.

## À quoi vous attendre

Ce projet est maintenu par une seule personne, bénévolement, qui peut être absente plusieurs semaines d'affilée. Aucun délai de réponse n'est garanti. L'objectif est de :

- accuser réception d'un signalement sous **30 jours** ;
- vous dire s'il est retenu, et ce qui est prévu, sous **60 jours** ;
- publier un correctif dans `latest` dès qu'il est prêt, puis un avis de sécurité GitHub qui vous crédite, sauf si vous préférez rester anonyme.

Si le signalement n'est pas retenu (non reproductible, hors périmètre ou comportement attendu), la raison vous est donnée. Merci de garder les détails privés jusqu'à la publication d'un correctif ou pendant 120 jours, selon la première échéance. Un silence de quelques semaines signifie que le mainteneur est absent, pas que le signalement est ignoré.

## Périmètre

Dans le périmètre :

- le code de ce dépôt et l'image Docker publiée ;
- la connexion et la gestion des sessions, l'API HTTP, le traitement des installeurs téléchargés ;
- les workflows de construction et de publication de ce dépôt.

Hors périmètre :

- Microsoft Intune, Microsoft Graph et Microsoft Entra ID eux-mêmes — à signaler à [Microsoft](https://msrc.microsoft.com/) ;
- un installeur malveillant que vous avez choisi de publier : l'outil publie ce qu'on lui donne ;
- ce qu'un administrateur Intune connecté peut faire avec des droits qu'il a déjà dans le portail Intune ;
- les déploiements qui ignorent les recommandations ci-dessous.

## Comment le projet est maintenu en sécurité

- **Dépendances** : les versions exactes sont notées dans un fichier de verrouillage. [Renovate](https://github.com/apps/renovate) ouvre une demande de fusion pour chaque mise à jour, immédiatement en cas de vulnérabilité connue. Une personne relit et fusionne chacune ; rien n'est fusionné automatiquement.
- **Analyse du code** : CodeQL analyse le code Python, l'interface et les workflows à chaque modification et chaque semaine.
- **Secrets** : la détection de secrets de GitHub et la protection à l'envoi sont activées sur le dépôt.
- **Image** : reconstruite à chaque modification et chaque lundi, pour que le système de base reçoive ses correctifs de sécurité. Elle tourne avec un utilisateur non privilégié, toutes les capacités Linux retirées.
- **Aucun secret stocké** : la connexion utilise le flux par code d'autorisation avec PKCE ; les jetons restent en mémoire, et chaque action est faite avec les droits de la personne connectée.

## Recommandations pour votre déploiement

- Gardez l'outil sur `localhost` ou sur un réseau interne. Ne l'exposez pas sur Internet sans nécessité.
- En dehors de `localhost`, utilisez le frontal HTTPS.
- Limitez qui peut se connecter (`ALLOWED_USERS`, ou *Affectation requise* sur l'application d'entreprise dans Entra ID).
- Mettez à jour régulièrement : `docker compose pull && docker compose up -d`.
- Avant de publier un installeur, comparez l'empreinte SHA-256 affichée sur l'écran de vérification à celle publiée par l'éditeur.
