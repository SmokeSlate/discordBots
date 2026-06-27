# SmokeBot script trigger: Cross-channel same-content spam detector
#
# Create with /script set:
#   name: cross_channel_spam
#   event: Message (all messages)
#   pattern: leave blank
#   channels: all
#   enabled: true
#
# This is meant to run in SmokeBot's script feature, not as an imported module.
# It uses the trusted script runtime tools/globals: bot, discord, os, asyncio, re.
#
# Optional env vars:
#   CROSS_SPAM_STAFF_CHANNEL_ID=123456789012345678
#   CROSS_SPAM_WINDOW_SECONDS=45
#   CROSS_SPAM_MIN_CHANNELS=4
#   CROSS_SPAM_ACCESSIBLE_RATIO=0.75
#   CROSS_SPAM_DELETE_LIMIT=50

import hashlib
import time


async def main():
    if event != "message_all" or message is None or guild is None or author is None:
        return

    if getattr(author, "bot", False):
        return

    if getattr(author.guild_permissions, "manage_messages", False):
        return

    staff_channel_id = int(os.getenv("CROSS_SPAM_STAFF_CHANNEL_ID", "0") or 0)
    window_seconds = int(os.getenv("CROSS_SPAM_WINDOW_SECONDS", "45") or 45)
    min_channels = int(os.getenv("CROSS_SPAM_MIN_CHANNELS", "4") or 4)
    accessible_ratio = float(os.getenv("CROSS_SPAM_ACCESSIBLE_RATIO", "0.75") or 0.75)
    delete_limit = int(os.getenv("CROSS_SPAM_DELETE_LIMIT", "50") or 50)

    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg")

    def normalize_text(value):
        value = (value or "").strip().lower()
        return re.sub(r"\s+", " ", value)

    def attachment_is_image(attachment):
        ctype = (getattr(attachment, "content_type", None) or "").lower()
        filename = (getattr(attachment, "filename", None) or "").lower()
        return ctype.startswith("image/") or filename.endswith(image_exts)

    async def image_hash(attachment):
        if not attachment_is_image(attachment):
            return None
        try:
            data = await attachment.read(use_cached=True)
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return "image-name:%s:%s" % (getattr(attachment, "filename", ""), getattr(attachment, "size", 0))

    text = normalize_text(message.content)
    image_hashes = []
    for attachment in getattr(message, "attachments", []) or []:
        h = await image_hash(attachment)
        if h:
            image_hashes.append(h)

    if not text and not image_hashes:
        return

    image_hashes.sort()
    signature_raw = "text:%s|images:%s" % (text, ",".join(image_hashes))
    signature = hashlib.sha256(signature_raw.encode("utf-8")).hexdigest()

    def member_can_send_in(ch):
        try:
            perms = ch.permissions_for(author)
            return bool(perms.view_channel and perms.send_messages)
        except Exception:
            return False

    accessible = 0
    for ch in guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.ForumChannel)) and member_can_send_in(ch):
            accessible += 1
    accessible = max(accessible, 1)
    required_channels = min(accessible, max(min_channels, round(accessible * accessible_ratio)))

    if not hasattr(bot, "cross_channel_spam_cache"):
        bot.cross_channel_spam_cache = {}
    if not hasattr(bot, "cross_channel_spam_active"):
        bot.cross_channel_spam_active = set()

    cache_key = (guild.id, author.id)
    now = time.time()
    cutoff = now - window_seconds
    entries = bot.cross_channel_spam_cache.get(cache_key, [])
    entries = [item for item in entries if item["timestamp"] >= cutoff]
    entries.append({
        "timestamp": now,
        "signature": signature,
        "channel_id": message.channel.id,
        "message": message,
        "content": message.content or "",
    })
    entries = entries[-100:]
    bot.cross_channel_spam_cache[cache_key] = entries

    matches = [item for item in entries if item["signature"] == signature]
    hit_channels = set(item["channel_id"] for item in matches)

    if len(hit_channels) < required_channels:
        return

    incident_key = (guild.id, author.id, signature)
    if incident_key in bot.cross_channel_spam_active:
        return

    bot.cross_channel_spam_active.add(incident_key)

    try:
        deleted = 0
        failed = 0
        for item in matches[:delete_limit]:
            try:
                await item["message"].delete()
                deleted += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.15)

        staff_channel = None
        if staff_channel_id:
            staff_channel = guild.get_channel(staff_channel_id)
            if staff_channel is None:
                try:
                    staff_channel = await bot.fetch_channel(staff_channel_id)
                except Exception:
                    staff_channel = None

        if staff_channel is not None:
            class CrossSpamBanView(discord.ui.View):
                def __init__(self, user_id):
                    super().__init__(timeout=86400)
                    self.user_id = int(user_id)

                @discord.ui.button(label="Ban User", style=discord.ButtonStyle.danger)
                async def ban_user(self, interaction, button):
                    if not interaction.user.guild_permissions.ban_members:
                        await interaction.response.send_message("You need Ban Members permission.", ephemeral=True)
                        return

                    try:
                        target = interaction.guild.get_member(self.user_id)
                        if target is None:
                            target = await interaction.client.fetch_user(self.user_id)
                        await interaction.guild.ban(
                            target,
                            reason="Cross-channel spam confirmed by %s" % interaction.user,
                            delete_message_seconds=86400,
                        )
                    except Exception as exc:
                        await interaction.response.send_message("Could not ban user: %s" % exc, ephemeral=True)
                        return

                    button.disabled = True
                    await interaction.response.edit_message(view=self)
                    await interaction.followup.send("Banned <@%s>." % self.user_id, allowed_mentions=discord.AllowedMentions.none())

            channel_list = " ".join("<#%s>" % cid for cid in sorted(hit_channels))
            preview = (message.content or "[image-only or attachment-only spam]").strip()[:900]
            fields = [
                ("User", "%s\n`%s`" % (author.mention, author.id), False),
                ("Channels Hit", "%s/%s required" % (len(hit_channels), required_channels), True),
                ("Messages Deleted", str(deleted), True),
                ("Delete Failures", str(failed), True),
                ("Channels", channel_list[:1000] or "Unknown", False),
                ("Content Preview", preview or "None", False),
            ]
            embed = discord.Embed(
                title="Cross-channel spam detected",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            for fname, fvalue, inline in fields:
                embed.add_field(name=fname, value=fvalue, inline=inline)
            embed.set_footer(text="Use the button below to ban after review.")
            await staff_channel.send(embed=embed, view=CrossSpamBanView(author.id))

        bot.cross_channel_spam_cache[cache_key] = []
    finally:
        bot.cross_channel_spam_active.discard(incident_key)


__script_async_entry__ = main()
