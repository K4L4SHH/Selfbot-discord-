import discord
from discord.ext import commands
import asyncio
import json
import os
import time
import traceback
from collections import defaultdict
from datetime import datetime

# ==================== CONFIGURATION ====================
# IMPORTANT: ne laisse pas de token en clair dans le fichier.
# Définit la variable d'environnement DISCORD_TOKEN ou remplace ci-dessous (risque de fuite).
TOKEN = os.getenv("YOUR TOKEN HERE")  # Ex: export DISCORD_TOKEN="ton_token_ici"
PREFIX = "&"
AUTOVOC_FILE = "autovoc-lisy.json"

# ==================== INITIALISATION ====================
# Intents (nécessaires selon la version de discord.py)
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

# Note: self-bot usage est généralement interdit par Discord (risque de ban)
try:
    client = commands.Bot(command_prefix=PREFIX, help_command=None, intents=intents, self_bot=True)
except TypeError:
    # Si la version de discord.py n'accepte pas self_bot param, on crée sans
    client = commands.Bot(command_prefix=PREFIX, help_command=None, intents=intents)

# Variables globales
autovoc_data = {}
voice_locks = {}
cooldowns = {}
reconnect_tasks = {}

# ==================== FICHIER JSON ====================
def load_autovoc():
    global autovoc_data
    if os.path.exists(AUTOVOC_FILE):
        try:
            with open(AUTOVOC_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # S'assurer que le contenu est bien un dict
            if isinstance(data, dict):
                autovoc_data = data
            else:
                print("⚠️ autovoc.json n'est pas un objet JSON (dict). Réinitialisation.")
                autovoc_data = {}
            print(f"✅ {len(autovoc_data)} autovoc chargés")
        except json.JSONDecodeError as e:
            print(f"❌ Erreur lecture JSON ({AUTOVOC_FILE}): {e}. Réinitialisation.")
            autovoc_data = {}
        except Exception as e:
            print(f"❌ Erreur lecture autovoc: {e}")
            traceback.print_exc()
            autovoc_data = {}
    else:
        autovoc_data = {}

def save_autovoc():
    try:
        with open(AUTOVOC_FILE, 'w', encoding='utf-8') as f:
            json.dump(autovoc_data or {}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Erreur sauvegarde: {e}")
        traceback.print_exc()

# ==================== CONNEXION VOCALE ====================
async def connect_voice(channel):
    """Connecte au canal vocal avec gestion d'erreurs"""
    if channel is None:
        print("❌ connect_voice: channel is None")
        return None

    guild = getattr(channel, "guild", None)
    if guild is None:
        print("❌ connect_voice: channel.guild est None")
        return None

    guild_id = str(guild.id)

    # Vérifier cooldown
    if guild_id in cooldowns and cooldowns[guild_id] > time.time():
        print(f"⏸️ Cooldown actif pour la guilde {guild.name}")
        return None

    try:
        # Vérifier si déjà connecté
        for vc in client.voice_clients:
            if vc.guild.id == channel.guild.id:
                if vc.channel.id == channel.id:
                    print(f"✅ Déjà connecté à {channel.name}")
                    return vc
                await vc.disconnect()
                await asyncio.sleep(0.5)

        # Connexion
        vc = await channel.connect()
        print(f"🔊 Connecté à {channel.guild.name} -> {channel.name}")
        return vc

    except Exception as e:
        print(f"❌ Erreur connexion: {e}")
        traceback.print_exc()
        return None

async def disconnect_voice(guild):
    """Déconnecte du vocal"""
    if guild is None:
        return
    try:
        for vc in list(client.voice_clients):
            if vc.guild.id == guild.id:
                await vc.disconnect()
                print(f"✅ Déconnecté de {guild.name}")
    except Exception as e:
        print(f"❌ Erreur déconnexion: {e}")
        traceback.print_exc()

# ==================== EVENTS ====================
@client.event
async def on_ready():
    load_autovoc()
    print("=" * 50)
    print(f"✅ Connecté: {client.user}")
    print(f"🆔 ID: {getattr(client.user, 'id', 'unknown')}")
    print(f"🔑 Prefix: {PREFIX}")
    print("=" * 50)
    print("⚠️  Self-bot = Violation ToS Discord")
    print("⚠️  Risque de ban permanent")
    print("=" * 50)

@client.event
async def on_voice_state_update(member, before, after):
    """Gère la reconnexion automatique"""
    try:
        if member.id != client.user.id:
            return

        # Récupérer la guild
        guild = None
        if after and after.channel:
            guild = after.channel.guild
        elif before and before.channel:
            guild = before.channel.guild

        if not guild:
            return

        guild_id = str(guild.id)

        # Vérifier si autovoc actif
        if not autovoc_data or guild_id not in autovoc_data:
            return

        target_channel_id = autovoc_data.get(guild_id)
        if target_channel_id is None:
            return

        current_channel_id = after.channel.id if (after and after.channel) else None

        # Déjà dans le bon canal
        if current_channel_id == target_channel_id:
            return

        # Reconnexion nécessaire
        if guild_id in cooldowns and cooldowns[guild_id] > time.time():
            return

        if guild_id in reconnect_tasks and not reconnect_tasks[guild_id].done():
            return

        async def reconnect():
            try:
                await asyncio.sleep(2)
                channel = client.get_channel(target_channel_id)
                if channel:
                    await connect_voice(channel)
                else:
                    print(f"⚠️ Channel {target_channel_id} introuvable lors de la reconnexion")
            finally:
                reconnect_tasks.pop(guild_id, None)

        reconnect_tasks[guild_id] = asyncio.create_task(reconnect())
    except Exception as e:
        print(f"❌ Erreur on_voice_state_update: {e}")
        traceback.print_exc()

# ==================== COMMANDES VOCALES ====================
@client.command()
async def autovoc(ctx, channel_id: int = None):
    """Active l'auto-reconnexion au canal vocal"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    # Déterminer le canal
    channel = None
    if channel_id:
        channel = client.get_channel(channel_id)
    elif ctx.author.voice:
        channel = ctx.author.voice.channel

    if not channel:
        print("❌ Canal introuvable")
        await safe_delete(ctx)
        return

    # Sauvegarder
    guild_id = str(ctx.guild.id)
    autovoc_data[guild_id] = channel.id
    save_autovoc()

    print(f"✅ Autovoc: {channel.guild.name} -> {channel.name}")
    await safe_delete(ctx)

    # Connexion initiale
    await connect_voice(channel)

@client.command()
async def autovoc_stop(ctx):
    """Désactive l'autovoc pour ce serveur"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    guild_id = str(ctx.guild.id)
    if guild_id in autovoc_data:
        del autovoc_data[guild_id]
        save_autovoc()

    await disconnect_voice(ctx.guild)
    print(f"ℹ️ Autovoc désactivé pour {ctx.guild.name}")
    await safe_delete(ctx)

@client.command()
async def autovoc_list(ctx):
    """Liste les autovoc actifs"""
    if not autovoc_data:
        try:
            await ctx.send("Aucun autovoc actif")
        except Exception:
            pass
        await safe_delete(ctx)
        return

    msg = "**📄 Autovoc actifs:**\n"
    for guild_id, channel_id in (autovoc_data.items() if isinstance(autovoc_data, dict) else []):
        guild = client.get_guild(int(guild_id)) if guild_id else None
        channel = client.get_channel(channel_id) if channel_id else None
        gname = guild.name if guild else f"Guild {guild_id}"
        cname = channel.name if channel else f"Canal {channel_id}"
        msg += f"• **{gname}** → {cname}\n"

    try:
        await ctx.send(msg)
    except Exception:
        # si l'envoi échoue, afficher en console
        print("⚠️ Impossible d'envoyer la liste d'autovoc au channel.")
    await safe_delete(ctx)

@client.command()
async def leave(ctx):
    """Quitte le canal vocal"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    guild_id = str(ctx.guild.id)

    # Désactiver autovoc
    if guild_id in autovoc_data:
        del autovoc_data[guild_id]
        save_autovoc()

    # Cooldown de 5 minutes
    cooldowns[guild_id] = time.time() + 300

    await disconnect_voice(ctx.guild)
    await safe_delete(ctx)

@client.command()
async def join(ctx, channel_id: int = None):
    """Rejoint un canal vocal"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    channel = None
    if channel_id:
        channel = client.get_channel(channel_id)
    elif ctx.author.voice:
        channel = ctx.author.voice.channel

    if channel:
        await connect_voice(channel)
    else:
        print("❌ Canal introuvable")

    await safe_delete(ctx)

# ==================== COMMANDES AUDIO ====================
@client.command()
async def mute(ctx):
    """Mute le micro (serveur)"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    try:
        if ctx.guild.me:
            await ctx.guild.me.edit(mute=True)
            print("🔇 Muted")
        else:
            print("⚠️ Impossible d'accéder à ctx.guild.me")
    except Exception as e:
        print(f"❌ {e}")
        traceback.print_exc()

    await safe_delete(ctx)

@client.command()
async def unmute(ctx):
    """Unmute le micro (serveur)"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    try:
        if ctx.guild.me:
            await ctx.guild.me.edit(mute=False)
            print("🔈 Unmuted")
        else:
            print("⚠️ Impossible d'accéder à ctx.guild.me")
    except Exception as e:
        print(f"❌ {e}")
        traceback.print_exc()

    await safe_delete(ctx)

@client.command()
async def deaf(ctx):
    """Active le deafen (serveur)"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    try:
        if ctx.guild.me:
            await ctx.guild.me.edit(deaf=True)
            print("🔕 Deafened")
        else:
            print("⚠️ Impossible d'accéder à ctx.guild.me")
    except Exception as e:
        print(f"❌ {e}")
        traceback.print_exc()

    await safe_delete(ctx)

@client.command()
async def undeaf(ctx):
    """Désactive le deafen (serveur)"""
    if not ctx.guild:
        await safe_delete(ctx)
        return

    try:
        if ctx.guild.me:
            await ctx.guild.me.edit(deaf=False)
            print("🔊 Undeafened")
        else:
            print("⚠️ Impossible d'accéder à ctx.guild.me")
    except Exception as e:
        print(f"❌ {e}")
        traceback.print_exc()

    await safe_delete(ctx)

# ==================== COMMANDES RPC ====================
@client.command()
async def rpc(ctx, activity_type: str = "playing", *, text: str):
    """Change la Rich Presence"""
    types = {
        "playing": discord.ActivityType.playing,
        "streaming": discord.ActivityType.streaming,
        "listening": discord.ActivityType.listening,
        "watching": discord.ActivityType.watching,
        "competing": discord.ActivityType.competing
    }

    act_type = types.get(activity_type.lower(), discord.ActivityType.playing)

    try:
        activity = discord.Activity(type=act_type, name=text)
        await client.change_presence(activity=activity)
        print(f"✅ RPC: {activity_type} - {text}")
    except Exception as e:
        print(f"❌ Erreur RPC: {e}")
        traceback.print_exc()

    await safe_delete(ctx)

@client.command()
async def rpc_game(ctx, *, game: str):
    """Définit un jeu"""
    try:
        activity = discord.Game(name=game)
        await client.change_presence(activity=activity)
        print(f"🎮 Jeu: {game}")
    except Exception as e:
        print(f"❌ {e}")
        traceback.print_exc()

    await safe_delete(ctx)

@client.command()
async def rpc_stop(ctx):
    """Arrête la RPC"""
    try:
        await client.change_presence(activity=None)
        print("ℹ️ RPC arrêtée")
    except Exception as e:
        print(f"❌ {e}")
        traceback.print_exc()

    await safe_delete(ctx)

# ==================== COMMANDES UTILES ====================
@client.command()
async def ping(ctx):
    """Affiche la latence"""
    latency = round(client.latency * 1000) if client.latency is not None else -1
    try:
        await ctx.send(f"🏓 Pong! {latency}ms")
    except Exception:
        pass
    await safe_delete(ctx)

@client.command()
async def help(ctx):
    """Affiche l'aide complète avec toutes les commandes"""
    embed = discord.Embed(
        title="📋 Commandes du Self-Bot",
        description=f"Prefix: `{PREFIX}` • Toutes les commandes disponibles",
        color=0x5865F2
    )

    # Auto-Vocal
    embed.add_field(
        name="🔊 Auto-Vocal",
        value=(
            f"`{PREFIX}autovoc [id]` - Active l'auto-reconnexion au canal vocal\n"
            f"*Si pas d'ID, utilise ton canal actuel*\n\n"
            f"`{PREFIX}autovoc_stop` - Désactive l'auto-reconnexion pour ce serveur\n\n"
            f"`{PREFIX}autovoc_list` - Liste tous les autovoc actifs sauvegardés\n\n"
            f"`{PREFIX}autovoc_remove [guild_id]` - Retire un autovoc\n"
            f"*Aliases: autovoc_rm, autovoc_delete*\n\n"
            f"`{PREFIX}join [id]` - Rejoint un canal vocal\n\n"
            f"`{PREFIX}leave` - Quitte le vocal + cooldown 5 min\n"
            f"*Aliases: quit, leavevc*"
        ),
        inline=False
    )

    # Audio
    embed.add_field(
        name="🔇 Contrôles Audio (Serveur)",
        value=(
            f"`{PREFIX}mute` - Mute ton micro côté serveur\n"
            f"*Alias: mic_mute*\n\n"
            f"`{PREFIX}unmute` - Unmute ton micro\n"
            f"*Alias: mic_unmute*\n\n"
            f"`{PREFIX}deaf` - Active le deafen (casque)\n\n"
            f"`{PREFIX}undeaf` - Désactive le deafen"
        ),
        inline=False
    )

    # RPC
    embed.add_field(
        name="🎮 Rich Presence (RPC)",
        value=(
            f"`{PREFIX}rpc <type> <texte>` - Change ton activité Discord\n"
            f"*Types: playing, streaming, listening, watching, competing*\n\n"
            f"`{PREFIX}rpc_game <jeu>` - Raccourci pour définir un jeu\n\n"
            f"`{PREFIX}rpc_stop` - Arrête complètement la RPC"
        ),
        inline=False
    )

    # Utilitaires
    embed.add_field(
        name="🔧 Utilitaires",
        value=(
            f"`{PREFIX}ping` - Affiche la latence du bot\n\n"
            f"`{PREFIX}help` - Affiche cette aide complète"
        ),
        inline=False
    )

    # Footer avec avertissements
    embed.set_footer(
        text="⚠️ Self-bot = Violation ToS Discord • Risque de ban permanent"
    )

    try:
        await ctx.send(embed=embed)
    except Exception as e:
        # Fallback en texte si l'embed ne passe pas
        fallback = f"""**📋 Commandes Self-Bot** (Prefix: `{PREFIX}`)

**🔊 Auto-Vocal**
• `{PREFIX}autovoc [id]` - Active auto-reconnexion
• `{PREFIX}autovoc_stop` - Désactive
• `{PREFIX}autovoc_list` - Liste les autovoc
• `{PREFIX}autovoc_remove [guild_id]` - Retire un autovoc
• `{PREFIX}join [id]` - Rejoint un canal
• `{PREFIX}leave` - Quitte le vocal

**🔇 Audio**
• `{PREFIX}mute` / `{PREFIX}unmute` - Micro serveur
• `{PREFIX}deaf` / `{PREFIX}undeaf` - Casque serveur

**🎮 RPC**
• `{PREFIX}rpc <type> <texte>` - Change présence
• `{PREFIX}rpc_game <jeu>` - Définit un jeu
• `{PREFIX}rpc_stop` - Arrête RPC

**🔧 Utilitaires**
• `{PREFIX}ping` - Latence
• `{PREFIX}help` - Cette aide

⚠️ Self-bot = Violation ToS Discord
"""
        try:
            await ctx.send(fallback)
        except Exception:
            print("❌ Erreur embed help (et fallback):", e)
            traceback.print_exc()

    await safe_delete(ctx)

# ==================== HELPERS ====================
async def safe_delete(ctx):
    try:
        if ctx and getattr(ctx, "message", None):
            await ctx.message.delete()
    except Exception:
        # suppression non critique, on ignore l'erreur
        pass

# ==================== GESTION ERREURS ====================
@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"❌ Erreur: {error}")
    traceback.print_exc()

# ==================== LANCEMENT ====================
if __name__ == "__main__":
    print("=" * 60)
    print("🚀 SELF-BOT DISCORD")
    print("=" * 60)
    print("⚠️  ATTENTION: Self-bots = BAN PERMANENT")
    print("=" * 60)

    if not TOKEN:
        print("\n❌ TOKEN NON CONFIGURÉ! Définis la variable d'environnement DISCORD_TOKEN.")
        print("Exemple (Linux/macOS): export DISCORD_TOKEN=\"ton_token_ici\"")
        print("⚠️  NE PARTAGE JAMAIS TON TOKEN")
        exit(1)

    load_autovoc()

    try:
        client.run(TOKEN)
    except discord.LoginFailure:
        print("\n❌ TOKEN INVALIDE")
        print("• Vérifie que c'est bien ton token UTILISATEUR (ou que tu utilises la bonne méthode)")
    except KeyboardInterrupt:
        print("\n👋 Arrêt...")
    except Exception as e:
        print(f"\n❌ ERREUR: {e}")
        traceback.print_exc()