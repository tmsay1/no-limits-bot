import os
import io
import re
import time
import math
import random
import sqlite3
import textwrap
from dataclasses import dataclass
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# =========================
# CONFIG (Your channel IDs)
# =========================
GIVEAWAYS_CHANNEL_ID = 1470319020598231160   # 🎁│giveaways
SHIP_CHANNEL_ID      = 1470319020732584010   # 🚢│ship
CONFESS_CHANNEL_ID   = 1470321471468339304   # 😶│confess

DB_PATH = os.path.join(os.path.dirname(__file__), "nolimits.sqlite3")

# =========================
# ENV
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
if not TOKEN:
    raise SystemExit("Missing DISCORD_TOKEN / TOKEN in .env")

# =========================
# BOT SETUP
# =========================
intents = discord.Intents.default()
# message_content not required for slash-only features
bot = commands.Bot(command_prefix="!", intents=intents)

def ts_now() -> int:
    return int(time.time())

def parse_duration_to_seconds(s: str) -> int:
    """
    Examples: 30m, 2h, 1d, 90m
    """
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([mhd])", s)
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    return 0

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    # Giveaway tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        prize TEXT NOT NULL,
        xp INTEGER NOT NULL DEFAULT 0,
        winners INTEGER NOT NULL DEFAULT 1,
        hosted_by INTEGER NOT NULL,
        image_url TEXT DEFAULT NULL,
        min_level INTEGER NOT NULL DEFAULT 0,
        ends_at INTEGER NOT NULL,
        ended INTEGER NOT NULL DEFAULT 0,
        ended_at INTEGER DEFAULT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS giveaway_entries (
        giveaway_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at INTEGER NOT NULL,
        PRIMARY KEY (giveaway_id, user_id)
    )
    """)

    # Confessions table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS confessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        author_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        thread_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    )
    """)

    conn.commit()
    conn.close()

# =========================
# FONTS (Ship Card)
# =========================
def _font(size: int, bold=False):
    # DejaVu usually exists on Ubuntu
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()

async def _fetch_avatar_bytes(member: discord.abc.User, size=256) -> bytes:
    asset = member.display_avatar.replace(size=size, static_format="png")
    return await asset.read()

def _circle_crop(img: Image.Image, size: int):
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out

def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill, outline=outline, width=width)

def render_ship_card(name1: str, name2: str, percent: int, avatar1: bytes, avatar2: bytes) -> bytes:
    W, H = 1100, 420
    bg = Image.new("RGBA", (W, H), (10, 12, 18, 255))

    # Soft gradient glow
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    gdraw.ellipse((-250, -250, 450, 450), fill=(255, 80, 140, 80))
    gdraw.ellipse((W-450, -250, W+250, 450), fill=(80, 170, 255, 70))
    gdraw.ellipse((W//2-300, H-150, W//2+300, H+350), fill=(255, 120, 80, 55))
    grad = grad.filter(ImageFilter.GaussianBlur(40))
    bg = Image.alpha_composite(bg, grad)

    draw = ImageDraw.Draw(bg)

    # Outer border
    border_color = (255, 105, 180, 190)
    _rounded_rect(draw, (20, 20, W-20, H-20), radius=26, fill=(15, 18, 28, 240), outline=border_color, width=3)

    title_f = _font(28, bold=True)
    pct_f = _font(58, bold=True)
    name_f = _font(24, bold=False)
    sub_f  = _font(20, bold=False)

    # Title
    title = "COMPATIBILITY"
    tw = draw.textlength(title, font=title_f)
    draw.text((W//2 - tw//2, 42), title, font=title_f, fill=(255, 160, 190, 255))

    # Avatars
    a1 = Image.open(io.BytesIO(avatar1)).convert("RGBA")
    a2 = Image.open(io.BytesIO(avatar2)).convert("RGBA")
    a1 = _circle_crop(a1, 130)
    a2 = _circle_crop(a2, 130)

    # Avatar rings
    ring = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((2, 2, 148, 148), outline=(255, 105, 180, 200), width=6)

    left_x = 235
    right_x = W - 365
    y = 120

    bg.paste(ring, (left_x-10, y-10), ring)
    bg.paste(a1, (left_x, y), a1)

    bg.paste(ring, (right_x-10, y-10), ring)
    bg.paste(a2, (right_x, y), a2)

    # Names
    n1 = name1[:18]
    n2 = name2[:18]
    draw.text((left_x + 65 - draw.textlength(n1, font=name_f)/2, y+145), n1, font=name_f, fill=(220, 230, 255, 255))
    draw.text((right_x + 65 - draw.textlength(n2, font=name_f)/2, y+145), n2, font=name_f, fill=(220, 230, 255, 255))

    # Middle percent + icon
    heart = "💔" if percent <= 25 else "💞" if percent <= 60 else "💖"
    draw.text((W//2 - 20, 135), heart, font=_font(34, bold=False), fill=(255, 160, 190, 255))
    pct_txt = f"{percent}%"
    pw = draw.textlength(pct_txt, font=pct_f)
    draw.text((W//2 - pw//2, 170), pct_txt, font=pct_f, fill=(255, 170, 200, 255))

    # Progress bar
    bar_x1, bar_y1, bar_x2, bar_y2 = 140, 295, W-140, 325
    _rounded_rect(draw, (bar_x1, bar_y1, bar_x2, bar_y2), radius=16, fill=(35, 40, 60, 220))
    fill_w = int((bar_x2 - bar_x1) * (percent / 100.0))
    _rounded_rect(draw, (bar_x1, bar_y1, bar_x1 + max(10, fill_w), bar_y2), radius=16, fill=(255, 120, 170, 240))

    # Tagline
    if percent <= 20:
        tagline = "Very unlikely..."
    elif percent <= 45:
        tagline = "Stranger things have happened."
    elif percent <= 70:
        tagline = "Could work 👀"
    elif percent <= 90:
        tagline = "Looking strong 💫"
    else:
        tagline = "Almost perfect! 💖"

    tw2 = draw.textlength(tagline, font=sub_f)
    draw.text((W//2 - tw2//2, 345), tagline, font=sub_f, fill=(190, 200, 230, 255))

    out = io.BytesIO()
    bg.save(out, format="PNG")
    return out.getvalue()

# =========================
# GIVEAWAY UI
# =========================
class ParticipateView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="Participate", style=discord.ButtonStyle.primary, emoji="🎉", custom_id="nl_gw_participate")
    async def participate(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only in giveaways channel
        if interaction.channel_id != GIVEAWAYS_CHANNEL_ID:
            await interaction.response.send_message("❌ هذا الأمر مخصص لروم 🎁 giveaways فقط.", ephemeral=True)
            return

        user_id = interaction.user.id
        conn = db()
        cur = conn.cursor()

        # verify giveaway exists & not ended
        cur.execute("SELECT ended FROM giveaways WHERE id=?", (self.giveaway_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            await interaction.response.send_message("❌ هذا القيف أواي غير موجود.", ephemeral=True)
            return
        if int(row[0]) == 1:
            conn.close()
            await interaction.response.send_message("⌛ هذا القيف أواي انتهى بالفعل.", ephemeral=True)
            return

        try:
            cur.execute("INSERT INTO giveaway_entries(giveaway_id, user_id, joined_at) VALUES(?,?,?)",
                        (self.giveaway_id, user_id, ts_now()))
            conn.commit()
            conn.close()
            await interaction.response.send_message("✅ تم تسجيل مشاركتك!", ephemeral=True)
        except sqlite3.IntegrityError:
            conn.close()
            await interaction.response.send_message("⚠️ أنت مشارك بالفعل.", ephemeral=True)

def giveaway_embed_active(host: discord.Member, prize: str, xp: int, winners: int, ends_at: int, min_level: int, image_url: Optional[str]):
    e = discord.Embed(
        title="🎉 Nitro & XP Giveaway [ENDED]" if False else "🎉 Nitro & XP Giveaway [STARTED]",
        description=(
            "🎊 **Nitro Giveaway — Thank You For The Support!**\n\n"
            f"To celebrate the activity, we’re gifting **{prize}**"
            + (f" + **{xp} XP**" if xp > 0 else "")
            + " to an active member of the community!\n\n"
            + (f"**Important:** Only members **Level {min_level}+** are eligible to win.\n" if min_level > 0 else "")
            + "\nClick the button below to participate 👇"
        ),
        color=discord.Color.gold()
    )
    e.add_field(name="🏆 Winner(s)", value=str(winners), inline=True)
    e.add_field(name="⏰ Ends", value=f"<t:{ends_at}:F>\n<t:{ends_at}:R>", inline=True)
    e.add_field(name="👤 Hosted by", value=host.mention, inline=False)
    if image_url:
        e.set_image(url=image_url)
    e.set_footer(text="No Limits Giveaways • Participate to join")
    return e

def giveaway_embed_ended(title: str, prize: str, xp: int, winners_mentions: str, entries: int, ended_at: int):
    e = discord.Embed(
        title=f"🎉 {title}",
        description="**This giveaway has ended!**",
        color=discord.Color.green()
    )
    # Make it look like the screenshot: big prize line
    prize_line = f"💎 **{prize}** 💎"
    if xp > 0:
        prize_line += f"\n✨ + **{xp} XP**"
    e.add_field(name="Prize:", value=prize_line, inline=False)
    e.add_field(name="Winner:", value=winners_mentions, inline=False)
    e.add_field(name="Entries:", value=str(entries), inline=True)
    e.set_footer(text=f"No Limits • {time.strftime('%Y-%m-%d %H:%M', time.gmtime(ended_at))} UTC")
    return e

def giveaway_results_embed(prize: str, xp: int, participants: int):
    e = discord.Embed(
        title="🎉 Nitro & XP Giveaway [RESULTS]",
        description="The winner of this giveaway is tagged above! Congratulations 🎉",
        color=discord.Color.blurple()
    )
    e.add_field(name="Prize", value="Discord Nitro" if "nitro" in prize.lower() else prize, inline=True)
    e.add_field(name="XP", value=str(xp) if xp > 0 else "—", inline=True)
    e.add_field(name="Participants", value=str(participants), inline=False)
    return e

def winner_announce_embed(winner: discord.Member, prize: str):
    e = discord.Embed(
        title="🏆 GIVEAWAY WINNER 🏆",
        description=f"Congratulations {winner.mention}!\n\nYou won **{prize}** in our giveaway!\n\nPlease DM a staff member to claim your prize! 🎁",
        color=discord.Color.gold()
    )
    e.set_thumbnail(url=winner.display_avatar.url)
    e.set_footer(text="No Limits • Claim your prize via staff")
    return e

# =========================
# COMMANDS
# =========================
@app_commands.default_permissions(manage_guild=True)
@bot.tree.command(name="giveaway_start", description="Start a pro giveaway with Participate button (No Limits style).")
@app_commands.describe(
    title="Example: 6,000 MEMBERS CELEBRATION",
    prize="Example: Discord Nitro",
    duration="30m / 2h / 1d",
    winners="Number of winners",
    xp="Optional XP reward",
    min_level="Optional min level requirement (display only)",
    image_url="Optional image/gif URL to show in embed"
)
async def giveaway_start(
    interaction: discord.Interaction,
    title: str,
    prize: str,
    duration: str,
    winners: int = 1,
    xp: int = 0,
    min_level: int = 0,
    image_url: Optional[str] = None
):
    if interaction.channel_id != GIVEAWAYS_CHANNEL_ID:
        await interaction.response.send_message("❌ استخدم الأمر داخل روم 🎁 giveaways فقط.", ephemeral=True)
        return

    seconds = parse_duration_to_seconds(duration)
    if seconds <= 0:
        await interaction.response.send_message("❌ مدة غير صحيحة. مثال: 30m أو 2h أو 1d", ephemeral=True)
        return
    if winners < 1 or winners > 20:
        await interaction.response.send_message("❌ winners لازم بين 1 و 20", ephemeral=True)
        return

    ends_at = ts_now() + seconds

    conn = db()
    cur = conn.cursor()
    # insert placeholder so we have giveaway id
    cur.execute("""
        INSERT INTO giveaways(guild_id, channel_id, message_id, prize, xp, winners, hosted_by, image_url, min_level, ends_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (interaction.guild_id, GIVEAWAYS_CHANNEL_ID, 0, prize, xp, winners, interaction.user.id, image_url, min_level, ends_at))
    giveaway_id = cur.lastrowid
    conn.commit()
    conn.close()

    view = ParticipateView(giveaway_id)
    embed = giveaway_embed_active(interaction.user, prize, xp, winners, ends_at, min_level, image_url)

    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()

    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, giveaway_id))
    conn.commit()
    conn.close()

    # Also pin the title visually by editing embed title
    embed.title = f"🎉 {title} 🎉"
    await msg.edit(embed=embed, view=view)

@app_commands.default_permissions(manage_guild=True)
@bot.tree.command(name="giveaway_end", description="End a giveaway early (by replying to its message).")
async def giveaway_end(interaction: discord.Interaction):
    if interaction.channel_id != GIVEAWAYS_CHANNEL_ID:
        await interaction.response.send_message("❌ استخدم الأمر داخل روم 🎁 giveaways فقط.", ephemeral=True)
        return
    await interaction.response.send_message("✅ ارسل رابط رسالة القيف أواي (Message Link) هنا.", ephemeral=True)

@app_commands.default_permissions(manage_guild=True)
@bot.tree.command(name="giveaway_reroll", description="Reroll winners for an ended giveaway (by message link).")
@app_commands.describe(message_link="Paste the giveaway message link")
async def giveaway_reroll(interaction: discord.Interaction, message_link: str):
    if interaction.channel_id != GIVEAWAYS_CHANNEL_ID:
        await interaction.response.send_message("❌ استخدم الأمر داخل روم 🎁 giveaways فقط.", ephemeral=True)
        return

    m = re.search(r"/channels/(\d+)/(\d+)/(\d+)", message_link)
    if not m:
        await interaction.response.send_message("❌ رابط غير صحيح.", ephemeral=True)
        return
    guild_id = int(m.group(1))
    channel_id = int(m.group(2))
    message_id = int(m.group(3))

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, prize, winners FROM giveaways WHERE guild_id=? AND channel_id=? AND message_id=?", (guild_id, channel_id, message_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        await interaction.response.send_message("❌ ما لقيت القيف أواي بهالرابط.", ephemeral=True)
        return
    giveaway_id, prize, winners = int(row[0]), row[1], int(row[2])

    cur.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
    users = [int(x[0]) for x in cur.fetchall()]
    conn.close()

    if not users:
        await interaction.response.send_message("❌ ما في مشاركين.", ephemeral=True)
        return

    winners = min(winners, len(users))
    picked = random.sample(users, winners)
    mentions = " ".join(f"<@{uid}>" for uid in picked)

    await interaction.channel.send(f"🔁 **REROLL WINNER(S):** {mentions}\n🎁 Prize: **{prize}**")
    await interaction.response.send_message("✅ تم الريرول.", ephemeral=True)

# =========================
# SHIP (Compatibility Card)
# =========================
@bot.tree.command(name="ship", description="Generate a pro compatibility card (No Limits style).")
@app_commands.describe(user="Select a user to ship with")
async def ship(interaction: discord.Interaction, user: discord.Member):
    if interaction.channel_id != SHIP_CHANNEL_ID:
        await interaction.response.send_message("❌ هذا الأمر مخصص لروم 🚢 ship فقط.", ephemeral=True)
        return
    if user.bot or interaction.user.bot:
        await interaction.response.send_message("❌ ما بنشحن بوتات 😄", ephemeral=True)
        return

    percent = random.randint(1, 100)
    a1 = await _fetch_avatar_bytes(interaction.user, size=256)
    a2 = await _fetch_avatar_bytes(user, size=256)
    img = render_ship_card(interaction.user.display_name, user.display_name, percent, a1, a2)

    file = discord.File(io.BytesIO(img), filename="compatibility.png")
    await interaction.response.send_message(file=file)

# =========================
# CONFESS (Anonymous + Thread)
# =========================
class ConfessModal(Modal, title="Anonymous Confession"):
    confession = TextInput(
        label="اكتب اعترافك (Anonymous)",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=2000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.channel_id != CONFESS_CHANNEL_ID:
            await interaction.response.send_message("❌ استخدم /confess داخل روم 😶 confess فقط.", ephemeral=True)
            return

        text = str(self.confession.value).strip()
        if not text:
            await interaction.response.send_message("❌ اكتب شي.", ephemeral=True)
            return

        conn = db()
        cur = conn.cursor()
        # generate confession number
        cur.execute("INSERT INTO confessions(guild_id, author_id, channel_id, thread_id, created_at) VALUES(?,?,?,?,?)",
                    (interaction.guild_id, interaction.user.id, CONFESS_CHANNEL_ID, 0, ts_now()))
        conf_id = cur.lastrowid
        conn.commit()

        channel = interaction.guild.get_channel(CONFESS_CHANNEL_ID)
        if not channel:
            conn.close()
            await interaction.response.send_message("❌ روم confess غير موجود.", ephemeral=True)
            return

        # Embed (dark + pro)
        e = discord.Embed(
            title=f"Anonymous Confession (#{conf_id})",
            description=text if len(text) <= 1200 else "Text is a bit long — posted as attachment.\nReply to this thread if you want to continue.",
            color=discord.Color.dark_green()
        )
        e.set_footer(text="Use /confess to share yours")
        e.set_author(name="Anonymous", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        file = None
        if len(text) > 1200:
            file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"confession-{conf_id}.txt")

        msg = await channel.send(embed=e, file=file)

        # Create thread per confession (Forum-like)
        thread = await msg.create_thread(name=f"Confession #{conf_id}", auto_archive_duration=1440)

        cur.execute("UPDATE confessions SET thread_id=? WHERE id=?", (thread.id, conf_id))
        conn.commit()
        conn.close()

        await thread.send("🗣️ **Reply here to continue this confession thread.**")
        await interaction.response.send_message(f"✅ تم نشر اعترافك (#{conf_id}) بشكل مجهول.", ephemeral=True)

@bot.tree.command(name="confess", description="Post an anonymous confession (creates a thread).")
async def confess(interaction: discord.Interaction):
    if interaction.channel_id != CONFESS_CHANNEL_ID:
        await interaction.response.send_message("❌ استخدم /confess داخل روم 😶 confess فقط.", ephemeral=True)
        return
    await interaction.response.send_modal(ConfessModal())

# Staff-only reveal (optional but pro moderation)
@app_commands.default_permissions(manage_guild=True)
@bot.tree.command(name="confess_reveal", description="(Staff) Reveal confession author by id.")
@app_commands.describe(confession_id="Confession number, like 120")
async def confess_reveal(interaction: discord.Interaction, confession_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT author_id, thread_id, created_at FROM confessions WHERE id=? AND guild_id=?", (confession_id, interaction.guild_id))
    row = cur.fetchone()
    conn.close()
    if not row:
        await interaction.response.send_message("❌ ما لقيت هذا الاعتراف.", ephemeral=True)
        return
    author_id, thread_id, created_at = int(row[0]), int(row[1]), int(row[2])
    await interaction.response.send_message(
        f"🕵️ Confession #{confession_id}\n"
        f"Author: <@{author_id}>\n"
        f"Thread: <#{thread_id}>\n"
        f"Time: <t:{created_at}:F>",
        ephemeral=True
    )

# =========================
# GIVEAWAY WATCHER (ENDED/RESULTS + WINNER POST)
# =========================
@tasks.loop(seconds=15)
async def giveaway_watcher():
    conn = db()
    cur = conn.cursor()

    now = ts_now()
    cur.execute("""
        SELECT id, guild_id, channel_id, message_id, prize, xp, winners, hosted_by, ends_at, image_url
        FROM giveaways
        WHERE ended=0 AND ends_at <= ?
        ORDER BY ends_at ASC
        LIMIT 10
    """, (now,))
    rows = cur.fetchall()

    for r in rows:
        gid, guild_id, channel_id, message_id = int(r[0]), int(r[1]), int(r[2]), int(r[3])
        prize, xp, winners, hosted_by = r[4], int(r[5]), int(r[6]), int(r[7])
        ends_at, image_url = int(r[8]), r[9]

        # entries
        cur.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (gid,))
        users = [int(x[0]) for x in cur.fetchall()]

        # mark ended
        cur.execute("UPDATE giveaways SET ended=1, ended_at=? WHERE id=?", (now, gid))
        conn.commit()

        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        # pick winners
        picked: List[int] = []
        if users:
            w = min(winners, len(users))
            picked = random.sample(users, w)

        mentions = " ".join(f"<@{uid}>" for uid in picked) if picked else "No valid participants."

        # fetch original message
        try:
            original = await channel.fetch_message(message_id)
        except Exception:
            original = None

        # edit original message to ENDED look
        ended_title = "GIVEAWAY ENDED"
        ended_embed = giveaway_embed_ended(
            title=ended_title,
            prize=prize,
            xp=xp,
            winners_mentions=mentions,
            entries=len(users),
            ended_at=now
        )

        if original:
            # disable button
            view = discord.ui.View()
            btn = discord.ui.Button(label="Participate", style=discord.ButtonStyle.secondary, emoji="🎉", disabled=True)
            view.add_item(btn)
            await original.edit(embed=ended_embed, view=view)

        # RESULTS post (like screenshot)
        await channel.send(content=mentions, embed=giveaway_results_embed(prize, xp, len(users)))

        # Winner announcement post (like screenshot)
        if picked:
            # announce first winner with thumbnail (pro look)
            winner_member = guild.get_member(picked[0]) or await guild.fetch_member(picked[0])
            await channel.send(embed=winner_announce_embed(winner_member, prize))

    conn.close()

@giveaway_watcher.before_loop
async def before_gw():
    await bot.wait_until_ready()

# =========================
# READY + SYNC
# =========================
@bot.event
async def on_ready():
    init_db()
    try:
        await bot.tree.sync()
    except Exception:
        pass
    if not giveaway_watcher.is_running():
        giveaway_watcher.start()
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")

bot.run(TOKEN)
