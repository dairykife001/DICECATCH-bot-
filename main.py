import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import random
import os

# ---------------- CONFIG ----------------
DROP_INTERVAL = 300
MEGA_DROP_COST = 4000
MEGA_DROP_COUNT = 5
MEGA_DROP_COUNTDOWN = 10
MEGA_DROP_SUMMARY_DELAY = 15
DATA_FILE = "dice_data.json"

# ---------------- BOT ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.messages = True
allowed_mentions = discord.AllowedMentions(roles=True, users=True, everyone=False)

bot = commands.Bot(command_prefix="!", intents=intents, allowed_mentions=allowed_mentions)
bot.remove_command("help")

# ---------------- DATA STORAGE ----------------
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"images": {}, "users": {}, "drop_channel": {}, "drop_role": {}}, f, indent=4)

with open(DATA_FILE, "r") as f:
    data = json.load(f)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def ensure_guild_user_entry(guild_id, user_id):
    if guild_id not in data["users"]:
        data["users"][guild_id] = {}
    if user_id not in data["users"][guild_id]:
        data["users"][guild_id][user_id] = {"coins":0, "points":0, "images":[]}

def get_next_dice_number_for_guild(guild_id):
    return len(data["images"].get(guild_id, [])) + 1

def user_has_dice(user_id, guild_id, dice_number):
    ensure_guild_user_entry(guild_id, user_id)
    return dice_number in data["users"][guild_id][user_id]["images"]

def grant_dice_to_user(user_id, guild_id, dice_number, coins=100, points=10):
    ensure_guild_user_entry(guild_id, user_id)
    if dice_number not in data["users"][guild_id][user_id]["images"]:
        data["users"][guild_id][user_id]["images"].append(dice_number)
        data["users"][guild_id][user_id]["coins"] += coins
        data["users"][guild_id][user_id]["points"] += points
        save_data()
        return True
    return False

def get_server_leaderboard(guild_id, top_n=10):
    users = data.get("users", {}).get(guild_id, {})
    rows = [(uid, u.get("points",0), u.get("coins",0), len(u.get("images",[]))) for uid,u in users.items()]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:top_n]

def get_global_leaderboard(top_n=10):
    totals = {}
    for guild_id, users in data.get("users", {}).items():
        for user_id, info in users.items():
            totals[user_id] = totals.get(user_id,0) + len(info.get("images",[]))
    return sorted(totals.items(), key=lambda x: x[1], reverse=True)[:top_n]

# ---------------- DROP ----------------
claimed_messages = set()
active_drops = set()
active_mega_drops = set()
mega_reactors = {}

async def send_drop(channel, guild_id, dice_name, image_url, mega_drop=False):
    s_gid = str(guild_id)

    if s_gid in active_drops:
        return

    active_drops.add(s_gid)

    role_id = data.get("drop_role", {}).get(s_gid)
    if role_id:
        await channel.send(f"<@&{role_id}>", allowed_mentions=discord.AllowedMentions(roles=True))

    title = f"{dice_name} Drop!" + (" — MEGA DROP!" if mega_drop else "")
    desc = "Everyone who reacts will collect this!" if mega_drop else "Be the first to react!"
    embed = discord.Embed(title=title, description=desc)
    embed.set_image(url=image_url)

    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction("🎲")
    except:
        pass

    if mega_drop:
        mega_reactors.setdefault(s_gid, {})[msg.id] = set()

    await asyncio.sleep(0.5)
    active_drops.remove(s_gid)
    return msg

# ---------------- DROP LOOP ----------------
@tasks.loop(seconds=DROP_INTERVAL)
async def drop_loop():
    for guild in bot.guilds:
        s_gid = str(guild.id)
        imgs = data.get("images", {}).get(s_gid, [])
        if not imgs:
            continue
        channel_id = data.get("drop_channel", {}).get(s_gid)
        if not channel_id:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        dice = random.choice(imgs)
        await send_drop(channel, s_gid, dice["name"], dice["url"])

# ---------------- REACTION HANDLING ----------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    emoji = str(reaction.emoji)
    if emoji != "🎲":
        return

    message = reaction.message
    if not message.embeds:
        return

    guild_id = str(message.guild.id)
    user_id = str(user.id)

    # Mega drop handling
    if guild_id in mega_reactors and message.id in mega_reactors[guild_id]:
        mega_reactors[guild_id][message.id].add(user_id)
        return

    # Normal drop
    if message.id in claimed_messages:
        return

    embed = message.embeds[0]
    dice_name = embed.title.split()[0]

    try:
        dice_number = int(dice_name.replace("Dice#", ""))
    except:
        return

    if user_has_dice(user_id, guild_id, dice_number):
        return

    claimed_messages.add(message.id)
    granted = grant_dice_to_user(user_id, guild_id, dice_number)

    if granted:
        await message.channel.send(
            f"🎉 Congratulations {user.mention}! You caught {dice_name} "
            f"and earned 100 coins + 10 points!"
        )

# ---------------- MEGA DROP COMMAND ----------------
@bot.tree.command(name="mega", description="Start a mega drop.")
async def mega(interaction: discord.Interaction):
    s_gid = str(interaction.guild.id)
    user_id = str(interaction.user.id)

    ensure_guild_user_entry(s_gid, user_id)

    if data["users"][s_gid][user_id]["coins"] < MEGA_DROP_COST:
        return await interaction.response.send_message("❌ Not enough coins!", ephemeral=True)

    if s_gid in active_mega_drops:
        return await interaction.response.send_message("❌ A mega drop is already running!", ephemeral=True)

    data["users"][s_gid][user_id]["coins"] -= MEGA_DROP_COST
    save_data()

    active_mega_drops.add(s_gid)

    channel_id = data.get("drop_channel", {}).get(s_gid)
    channel = interaction.guild.get_channel(channel_id) or interaction.channel

    # Countdown
    msg = await channel.send(f"🎲 Mega drop starting in {MEGA_DROP_COUNTDOWN} seconds!")
    for i in range(MEGA_DROP_COUNTDOWN, 0, -1):
        await msg.edit(content=f"🎲 Mega drop starting in {i} seconds!")
        await asyncio.sleep(1)
    await msg.delete()

    imgs = data.get("images", {}).get(s_gid, [])
    if not imgs:
        active_mega_drops.remove(s_gid)
        return await interaction.response.send_message("❌ No images.", ephemeral=True)

    mega_messages = []

    for _ in range(MEGA_DROP_COUNT):
        dice = random.choice(imgs)
        m = await send_drop(channel, s_gid, dice["name"], dice["url"], mega_drop=True)
        mega_messages.append((m, int(dice["name"].replace("Dice#", ""))))
        await asyncio.sleep(1)

    await asyncio.sleep(MEGA_DROP_SUMMARY_DELAY)

    summary = {}

    for msg, dice_number in mega_messages:
        reactors = mega_reactors.get(s_gid, {}).get(msg.id, set())

        for u_id in reactors:
            ensure_guild_user_entry(s_gid, u_id)
            summary.setdefault(u_id, {"new":0,"dupes":0})

            if dice_number not in data["users"][s_gid][u_id]["images"]:
                data["users"][s_gid][u_id]["images"].append(dice_number)
                data["users"][s_gid][u_id]["coins"] += 100
                data["users"][s_gid][u_id]["points"] += 10
                summary[u_id]["new"] += 1
            else:
                summary[u_id]["dupes"] += 1

    save_data()

    text = f"🔥 Mega Drop Complete!\n"
    for u_id, counts in summary.items():
        user = await bot.fetch_user(int(u_id))
        text += f"**{user.name}** — New: {counts['new']}, Dupes: {counts['dupes']}\n"

    await channel.send(text)

    active_mega_drops.remove(s_gid)
    await interaction.response.send_message("✅ Mega drop finished!", ephemeral=True)

# ---------------- ADD COINS ----------------
@bot.tree.command(name="addcoins", description="Admin: add coins to a user.")
@app_commands.checks.has_permissions(administrator=True)
async def addcoins(interaction: discord.Interaction, user: discord.Member, amount: int):
    s_gid = str(interaction.guild.id)
    u_id = str(user.id)
    ensure_guild_user_entry(s_gid, u_id)

    data["users"][s_gid][u_id]["coins"] += amount
    save_data()

    await interaction.response.send_message(
        f"Added {amount} coins to {user.name}.",
        ephemeral=True
    )

# ---------------- ADD IMAGE ----------------
@commands.has_permissions(administrator=True)
@bot.command(name="addimage")
async def addimage_cmd(ctx):
    s_gid = str(ctx.guild.id)

    if not ctx.message.attachments:
        return await ctx.send("❌ Attach at least one image.")

    data["images"].setdefault(s_gid, [])

    start = get_next_dice_number_for_guild(s_gid)

    for att in ctx.message.attachments:
        num = get_next_dice_number_for_guild(s_gid)
        data["images"][s_gid].append({"name": f"Dice#{num}", "url": att.url})

    save_data()

    end = get_next_dice_number_for_guild(s_gid) - 1
    await ctx.send(f"Added Dice#{start} → Dice#{end}")

# ---------------- MANUAL DROP ----------------
@bot.tree.command(name="drop", description="Drop a dice manually.")
async def drop(interaction: discord.Interaction):
    s_gid = str(interaction.guild.id)
    imgs = data.get("images", {}).get(s_gid, [])

    if not imgs:
        return await interaction.response.send_message("No images.", ephemeral=True)

    dice = random.choice(imgs)
    channel_id = data["drop_channel"].get(s_gid)
    channel = interaction.guild.get_channel(channel_id) or interaction.channel

    await send_drop(channel, s_gid, dice["name"], dice["url"])
    await interaction.response.send_message("Drop sent!", ephemeral=True)

# ---------------- LEADERBOARD ----------------
@bot.tree.command(name="leaderboard", description="Server leaderboard.")
async def leaderboard(interaction: discord.Interaction):
    s_gid = str(interaction.guild.id)
    rows = get_server_leaderboard(s_gid)

    if not rows:
        return await interaction.response.send_message("No data yet.", ephemeral=True)

    text = "**Leaderboard:**\n"
    for i, (uid, pts, coins, count) in enumerate(rows, start=1):
        user = await bot.fetch_user(int(uid))
        text += f"{i}. {user.name} — {pts} pts, {coins} coins, {count} images\n"

    await interaction.response.send_message(text, ephemeral=True)

# ---------------- GLOBAL LEADERBOARD ----------------
@bot.tree.command(name="global", description="Global image ranking.")
async def global_lb(interaction: discord.Interaction):
    rows = get_global_leaderboard()

    text = "**Global:**\n"
    for i, (uid, total) in enumerate(rows, start=1):
        user = await bot.fetch_user(int(uid))
        text += f"{i}. {user.name} — {total} images\n"

    await interaction.response.send_message(text, ephemeral=True)

# ---------------- READY EVENT ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.tree.sync()
    print("Slash commands synced.")

    if not drop_loop.is_running():
        drop_loop.start()
        print("Drop loop started.")

# ---------------- RUN BOT ----------------
TOKEN = os.getenv("TOKEN")
bot.run(TOKEN)
