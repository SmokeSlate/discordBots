# SmokeBot Privacy Policy

_Last updated: 12/8/2025_

SmokeBot is a Discord moderation and support assistant developed for deployment by individual server owners. This Privacy Policy explains what limited information the bot processes when it operates in a Discord server, how that information is used, and the choices available to server administrators and members.

## 1. Information We Process
SmokeBot only accesses data that Discord makes available to bots in servers where it has been invited. The bot does not collect information outside of Discord. Depending on the features that have been enabled by server administrators, SmokeBot may process:

### 1.1 Ticketing system
* Discord user IDs for the member who created the ticket and staff who interact with it
* Category selection, ticket status, creation and closure timestamps
* The channel or thread ID that hosts the ticket conversation
* Any messages voluntarily posted in the ticket thread (handled directly on Discord – not stored by the bot)

### 1.2 Reaction roles
* Message IDs, emoji identifiers, and the role ID that should be granted or removed
* Guild (server) IDs necessary to apply the role

### 1.3 Snippets
* Per‑guild snippet triggers and the text that should be posted when a trigger is used
* Whether a snippet is dynamic and any placeholder structure configured by server staff

### 1.4 Persistent pins
* The text content configured for the persistent pin
* The IDs for the message, channel, guild, and the administrator who set the pin

### 1.5 Moderation utilities
* When moderation commands such as timeout, kick, ban, or message purge are run, SmokeBot processes the relevant Discord IDs and command parameters in memory to complete the request. These actions are executed against Discord’s APIs and are not stored by the bot after completion.

SmokeBot requires access to message content in servers where administrators enable snippet functionality or pin reposting. This access is limited to reading messages in channels where the bot has permission so that it can detect snippet triggers and keep tracked pins up to date.

## 2. How We Use Information
The information described above is used solely to:

* Provide and manage the ticketing, snippet, pin, reaction role, and moderation features requested by server administrators
* Maintain command configuration across restarts by storing the minimum required settings in local JSON files on the host machine (`ticket_data.json`, `reaction_roles.json`, `snippets.json`, `pinned_messages.json`, `ticket_categories.json`)
* Respond to slash commands and interaction events routed through Discord

SmokeBot does not use the data for analytics, advertising, or any secondary purposes.

## 3. Where Information Is Stored
All configuration data is stored locally on the machine that hosts SmokeBot. No data is transmitted to external third parties other than Discord’s platform APIs. Server owners are responsible for securing the machine where the bot runs, including restricting access to the JSON configuration files mentioned above.

## 4. Data Retention and Deletion
Stored configuration data remains in place until a server administrator edits or removes it (for example, by deleting a snippet or clearing ticket history) or until the underlying JSON files are deleted from the host machine. Ticket threads themselves remain on Discord until archived or deleted by server staff.

Server administrators may request removal of stored configuration data at any time by deleting the corresponding JSON files or by contacting the bot operator. Because SmokeBot is self‑hosted, the operator is typically the server owner or maintainer who deployed the bot.

## 5. Legal Basis
For servers operating under the EU/EEA, SmokeBot processes the limited personal data described above under the legitimate interest of the server administrators who deploy the bot to moderate and support their community.

## 6. Security
The bot relies on the hosting operator to provide physical and network security. SmokeBot itself restricts access to stored data by only reading and writing to the local file system. Administrators should keep the hosting environment up to date, safeguard the Discord bot token, and limit operating system access to trusted individuals.

## 7. Children’s Privacy
SmokeBot does not knowingly collect personal information from children. Discord requires users to be at least 13 years old (or the minimum age in their jurisdiction) to use the platform. Server owners should ensure their communities comply with Discord’s Terms of Service.

## 8. Third‑Party Services
SmokeBot interacts exclusively with Discord’s APIs. Use of the bot is also subject to Discord’s own policies, including the [Discord Terms of Service](https://discord.com/terms) and [Privacy Policy](https://discord.com/privacy).

## 9. Changes to This Policy
We may update this Privacy Policy to reflect improvements or legal requirements. When changes are made, the “Last updated” date at the top of this page will be revised. Material changes will be communicated to server administrators through the project repository or release notes.

## 10. Contact
Because SmokeBot is typically self‑hosted, the primary contact for privacy requests is the individual or team that operates the bot in your server. If you obtained SmokeBot from a public repository, please use the contact information provided there to reach the maintainer.

---
By continuing to use SmokeBot in your server, you acknowledge that you have read and understood this Privacy Policy.
