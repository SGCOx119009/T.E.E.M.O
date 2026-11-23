"""
core/localization.py - the language of the product, and the place it lives.

THE PROBLEM THIS EXISTS FOR
    A voice assistant that only speaks English is an assistant that only
    works for the people who speak English. The person who asks for the
    weather in Tacna wants the answer in Spanish, the person in Paris wants
    it in French, and the person in Shanghai wants it in Chinese. The
    interface has to follow too: a button that says "Retry" to a person who
    cannot read it is not a button, it is a decoration.

WHAT THIS MODULE IS
    One catalog of strings in five languages, one stored language, and one
    stored region (city + country). The interface asks for a string by key;
    the model is told, in English, which language to answer in and which
    place the user is in.

WHY THE TRANSLATIONS ARE WRITTEN BY HAND
    A machine translation of "The task did not happen" is usually fine and
    sometimes subtly wrong, and "subtly wrong" is the expensive kind: the
    sentence is grammatical, it is understood, and it says something else.
    These strings are short, they are seen every day, and they were written
    one at a time by somebody who speaks the language.

TWO RULES THAT MUST NOT BE BROKEN
    * No placeholder may exist in one language and be missing in another.
      A "{city}" that survives in English and vanishes in French produces an
      empty sentence, and no test would notice unless one asks for exactly
      that.
    * `t()` and `translate()` NEVER raise. A missing translation has to
      degrade to English, and a missing key has to degrade to the key
      itself. The window that asks for a string must never die asking for
      it.

THE REGION
    The region is not decoration either. "What is the weather like?" has no
    answer without a place, and "give me the news" means something
    different in Tacna than in Santiago. So the region is a city, a country
    and, when we know it, the coordinates that go with it. A city that is
    not in the table is still accepted: a person is allowed to live
    somewhere the product has never heard of.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from memory import config_manager

# ── Languages ────────────────────────────────────────────────────────────────

DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class Language:
    """One language the product speaks.

    `name_en` is for the settings panel of an English installation;
    `name_native` is what a person calls the language in the language
    itself, because "Portuguese" is not how a Portuguese speaker asks for
    Portuguese.
    """

    code: str
    name_en: str
    name_native: str


LANGUAGES: tuple[Language, ...] = (
    Language(code="en", name_en="English", name_native="English"),
    Language(code="es", name_en="Spanish", name_native="Español"),
    Language(code="pt", name_en="Portuguese", name_native="Português"),
    Language(code="fr", name_en="French", name_native="Français"),
    Language(code="zh", name_en="Chinese", name_native="简体中文"),
)

_LANGUAGE_CODES = frozenset(lang.code for lang in LANGUAGES)


def language_codes() -> list[str]:
    """The codes of every language the product ships, in order."""
    return [lang.code for lang in LANGUAGES]


def _language_object(code: str) -> Language:
    """The Language of that code, or the default one."""
    wanted = str(code or "").strip().lower()
    for lang in LANGUAGES:
        if lang.code == wanted:
            return lang
    return LANGUAGES[0]


def _normalize_lang(code: str | None) -> str | None:
    """A code we recognise, or None."""
    wanted = str(code or "").strip().lower()
    return wanted if wanted in _LANGUAGE_CODES else None


# ── The catalog ──────────────────────────────────────────────────────────────
# Every key has all five languages. The English value is the reference: the
# other four say the same thing in the other language, with the same
# placeholder names.

CATALOG: dict[str, dict[str, str]] = {
    # -- system / runtime
    "app.name": {
        "en": "T.E.E.M.O.",
        "es": "T.E.E.M.O.",
        "pt": "T.E.E.M.O.",
        "fr": "T.E.E.M.O.",
        "zh": "T.E.E.M.O.",
    },
    "app.tagline": {
        "en": "A voice assistant that lives on your desktop.",
        "es": "Un asistente de voz que vive en tu escritorio.",
        "pt": "Um assistente de voz que vive na sua área de trabalho.",
        "fr": "Un assistant vocal qui vit sur votre bureau.",
        "zh": "常驻桌面的语音助手。",
    },
    "app.ready": {
        "en": "Ready.",
        "es": "Listo.",
        "pt": "Pronto.",
        "fr": "Prêt.",
        "zh": "已就绪。",
    },
    "app.listening": {
        "en": "Listening.",
        "es": "Escuchando.",
        "pt": "Ouvindo.",
        "fr": "À l'écoute.",
        "zh": "正在聆听。",
    },
    "app.stopped": {
        "en": "Stopped.",
        "es": "Detenido.",
        "pt": "Parado.",
        "fr": "Arrêté.",
        "zh": "已停止。",
    },
    "app.error": {
        "en": "Something went wrong.",
        "es": "Algo salió mal.",
        "pt": "Algo deu errado.",
        "fr": "Une erreur est survenue.",
        "zh": "出现问题。",
    },
    "app.ready_short": {
        "en": "Ready",
        "es": "Listo",
        "pt": "Pronto",
        "fr": "Prêt",
        "zh": "就绪",
    },
    "app.version": {
        "en": "App version {version}",
        "es": "Versión de la aplicación {version}",
        "pt": "Versão do aplicativo {version}",
        "fr": "Version de l'application {version}",
        "zh": "应用版本 {version}",
    },

    # -- settings panel
    "settings.title": {
        "en": "Settings",
        "es": "Ajustes",
        "pt": "Configurações",
        "fr": "Paramètres",
        "zh": "设置",
    },
    "settings.language": {
        "en": "Language",
        "es": "Idioma",
        "pt": "Idioma",
        "fr": "Langue",
        "zh": "语言",
    },
    # ── Which player a music request goes to ─────────────────────────────────
    "music.service": {
        "en": "MUSIC PLAYER",
        "es": "REPRODUCTOR DE MÚSICA",
        "pt": "REPRODUTOR DE MÚSICA",
        "fr": "LECTEUR DE MUSIQUE",
        "zh": "音乐播放器",
    },
    "music.service_auto": {
        "en": "Automatic",
        "es": "Automático",
        "pt": "Automático",
        "fr": "Automatique",
        "zh": "自动",
    },
    "music.service_spotify": {
        "en": "Spotify",
        "es": "Spotify",
        "pt": "Spotify",
        "fr": "Spotify",
        "zh": "Spotify",
    },
    "music.service_youtube": {
        "en": "YouTube",
        "es": "YouTube",
        "pt": "YouTube",
        "fr": "YouTube",
        "zh": "YouTube",
    },
    "music.service_tip": {
        "en": "Where a song is opened. Automatic uses Spotify when it is "
              "installed on this computer, and YouTube otherwise.",
        "es": "Dónde se abre una canción. Automático usa Spotify si está "
              "instalado en este equipo, y YouTube si no.",
        "pt": "Onde uma música é aberta. Automático usa o Spotify se ele "
              "estiver instalado neste computador, e o YouTube caso contrário.",
        "fr": "Où un morceau est ouvert. Automatique utilise Spotify s'il "
              "est installé sur cet ordinateur, et YouTube sinon.",
        "zh": "歌曲在哪里打开。自动：安装了 Spotify 就用 Spotify，"
              "否则用 YouTube。",
    },
    "settings.language_help": {
        "en": "The language used in the interface and in every answer.",
        "es": "El idioma de la interfaz y de todas las respuestas.",
        "pt": "O idioma da interface e de todas as respostas.",
        "fr": "La langue de l'interface et de toutes les réponses.",
        "zh": "界面和所有回答使用的语言。",
    },
    "settings.language_value": {
        "en": "Selected language: {language}",
        "es": "Idioma seleccionado: {language}",
        "pt": "Idioma selecionado: {language}",
        "fr": "Langue sélectionnée : {language}",
        "zh": "已选语言：{language}",
    },
    "settings.region": {
        "en": "Region",
        "es": "Región",
        "pt": "Região",
        "fr": "Région",
        "zh": "地区",
    },
    "settings.region_city": {
        "en": "City",
        "es": "Ciudad",
        "pt": "Cidade",
        "fr": "Ville",
        "zh": "城市",
    },
    "settings.region_country": {
        "en": "Country",
        "es": "País",
        "pt": "País",
        "fr": "Pays",
        "zh": "国家",
    },
    "settings.region_help": {
        "en": "Used for local weather and regional news.",
        "es": "Se usa para el clima local y las noticias regionales.",
        "pt": "Usado para o clima local e as notícias regionais.",
        "fr": "Utilisé pour la météo locale et les nouvelles régionales.",
        "zh": "用于本地天气和地区新闻。",
    },
    "settings.save": {
        "en": "Save",
        "es": "Guardar",
        "pt": "Salvar",
        "fr": "Enregistrer",
        "zh": "保存",
    },
    "settings.saved": {
        "en": "Saved.",
        "es": "Guardado.",
        "pt": "Salvo.",
        "fr": "Enregistré.",
        "zh": "已保存。",
    },
    "settings.cancel": {
        "en": "Cancel",
        "es": "Cancelar",
        "pt": "Cancelar",
        "fr": "Annuler",
        "zh": "取消",
    },
    "settings.wake_phrase": {
        "en": "Wake phrase",
        "es": "Frase de activación",
        "pt": "Frase de ativação",
        "fr": "Phrase d'activation",
        "zh": "唤醒短语",
    },
    "settings.wake_mode": {
        "en": "Wake mode",
        "es": "Modo de activación",
        "pt": "Modo de ativação",
        "fr": "Mode d'activation",
        "zh": "唤醒方式",
    },
    "settings.voice": {
        "en": "Voice",
        "es": "Voz",
        "pt": "Voz",
        "fr": "Voix",
        "zh": "声音",
    },
    "settings.scale": {
        "en": "Interface scale",
        "es": "Escala de la interfaz",
        "pt": "Escala da interface",
        "fr": "Échelle de l'interface",
        "zh": "界面缩放",
    },
    "settings.providers": {
        "en": "Providers",
        "es": "Proveedores",
        "pt": "Provedores",
        "fr": "Fournisseurs",
        "zh": "服务商",
    },

    # -- common actions
    "action.ok": {
        "en": "Ok",
        "es": "Ok",
        "pt": "Ok",
        "fr": "Ok",
        "zh": "确定",
    },
    "action.cancel": {
        "en": "Cancel",
        "es": "Cancelar",
        "pt": "Cancelar",
        "fr": "Annuler",
        "zh": "取消",
    },
    "action.close": {
        "en": "Close",
        "es": "Cerrar",
        "pt": "Fechar",
        "fr": "Fermer",
        "zh": "关闭",
    },
    "action.save": {
        "en": "Save",
        "es": "Guardar",
        "pt": "Salvar",
        "fr": "Enregistrer",
        "zh": "保存",
    },
    "action.retry": {
        "en": "Retry",
        "es": "Reintentar",
        "pt": "Tentar de novo",
        "fr": "Réessayer",
        "zh": "重试",
    },
    "action.copy": {
        "en": "Copy",
        "es": "Copiar",
        "pt": "Copiar",
        "fr": "Copier",
        "zh": "复制",
    },
    "action.paste": {
        "en": "Paste",
        "es": "Pegar",
        "pt": "Colar",
        "fr": "Coller",
        "zh": "粘贴",
    },
    "action.open": {
        "en": "Open",
        "es": "Abrir",
        "pt": "Abrir",
        "fr": "Ouvrir",
        "zh": "打开",
    },
    "action.refresh": {
        "en": "Refresh",
        "es": "Actualizar",
        "pt": "Atualizar",
        "fr": "Actualiser",
        "zh": "刷新",
    },
    "action.send": {
        "en": "Send",
        "es": "Enviar",
        "pt": "Enviar",
        "fr": "Envoyer",
        "zh": "发送",
    },
    "action.delete": {
        "en": "Delete",
        "es": "Eliminar",
        "pt": "Excluir",
        "fr": "Supprimer",
        "zh": "删除",
    },
    "action.confirm": {
        "en": "Confirm",
        "es": "Confirmar",
        "pt": "Confirmar",
        "fr": "Confirmer",
        "zh": "确认",
    },
    "action.back": {
        "en": "Back",
        "es": "Atrás",
        "pt": "Voltar",
        "fr": "Retour",
        "zh": "返回",
    },
    "action.next": {
        "en": "Next",
        "es": "Siguiente",
        "pt": "Próximo",
        "fr": "Suivant",
        "zh": "下一步",
    },

    # -- hud / chat
    "hud.thinking": {
        "en": "Thinking",
        "es": "Pensando",
        "pt": "Pensando",
        "fr": "Réflexion",
        "zh": "思考中",
    },
    "hud.listening": {
        "en": "Listening",
        "es": "Escuchando",
        "pt": "Ouvindo",
        "fr": "À l'écoute",
        "zh": "正在聆听",
    },
    "hud.speaking": {
        "en": "Speaking",
        "es": "Hablando",
        "pt": "Falando",
        "fr": "En train de parler",
        "zh": "正在说话",
    },
    "hud.idle": {
        "en": "Idle",
        "es": "Inactivo",
        "pt": "Ocioso",
        "fr": "Inactif",
        "zh": "空闲",
    },
    "hud.processing": {
        "en": "Processing",
        "es": "Procesando",
        "pt": "Processando",
        "fr": "Traitement en cours",
        "zh": "处理中",
    },
    "hud.error": {
        "en": "Error",
        "es": "Fallo",
        "pt": "Erro",
        "fr": "Erreur",
        "zh": "错误",
    },
    "chat.you": {
        "en": "You",
        "es": "Tú",
        "pt": "Você",
        "fr": "Vous",
        "zh": "你",
    },
    "chat.assistant": {
        "en": "Assistant",
        "es": "Asistente",
        "pt": "Assistente",
        "fr": "Aide",
        "zh": "助手",
    },
    "chat.empty": {
        "en": "No messages yet.",
        "es": "Todavía no hay mensajes.",
        "pt": "Ainda não há mensagens.",
        "fr": "Aucun message pour l'instant.",
        "zh": "还没有消息。",
    },
    "chat.placeholder": {
        "en": "Write a message",
        "es": "Escribe un mensaje",
        "pt": "Escreva uma mensagem",
        "fr": "Écrivez un message",
        "zh": "输入消息",
    },
    "chat.copy": {
        "en": "Copy",
        "es": "Copiar",
        "pt": "Copiar",
        "fr": "Copier",
        "zh": "复制",
    },
    "chat.copied": {
        "en": "Copied.",
        "es": "Copiado.",
        "pt": "Copiado.",
        "fr": "Copié.",
        "zh": "已复制。",
    },

    # -- tools and their outcomes (core/outcome.py statuses)
    "tool.done": {
        "en": "Done",
        "es": "Hecho",
        "pt": "Feito",
        "fr": "Terminé",
        "zh": "完成",
    },
    "tool.unverified": {
        "en": "Unverified",
        "es": "Sin verificar",
        "pt": "Não verificado",
        "fr": "Non vérifié",
        "zh": "未确认",
    },
    "tool.failed": {
        "en": "Failed",
        "es": "Falló",
        "pt": "Falhou",
        "fr": "Échec",
        "zh": "失败",
    },
    "tool.ask": {
        "en": "Needs an answer",
        "es": "Necesita una respuesta",
        "pt": "Precisa de uma resposta",
        "fr": "Nécessite une réponse",
        "zh": "需要回答",
    },
    "tool.started": {
        "en": "Started",
        "es": "Iniciado",
        "pt": "Iniciado",
        "fr": "Démarré",
        "zh": "已开始",
    },
    "tool.done_help": {
        "en": "The task was done and the result was checked.",
        "es": "La tarea se hizo y el resultado se comprobó.",
        "pt": "A tarefa foi feita e o resultado foi conferido.",
        "fr": "La tâche a été faite et le résultat a été vérifié.",
        "zh": "任务已完成并已核对结果。",
    },
    "tool.unverified_help": {
        "en": "The request was sent but the result could not be confirmed.",
        "es": "La solicitud se envió pero no se pudo confirmar el resultado.",
        "pt": "A solicitação foi enviada, mas o resultado não pôde ser confirmado.",
        "fr": "La demande a été envoyée, mais le résultat n'a pas pu être confirmé.",
        "zh": "请求已发出，但无法确认结果。",
    },
    "tool.failed_help": {
        "en": "The task did not happen. Tell the user the reason.",
        "es": "La tarea no ocurrió. Dile al usuario el motivo.",
        "pt": "A tarefa não aconteceu. Diga ao usuário o motivo.",
        "fr": "La tâche n'a pas eu lieu. Indiquez la raison à l'utilisateur.",
        "zh": "任务没有完成，请告知用户原因。",
    },
    "tool.ask_help": {
        "en": "Nothing was done yet. Ask the user and wait for the answer.",
        "es": "Todavía no se hizo nada. Pregunta al usuario y espera la respuesta.",
        "pt": "Nada foi feito ainda. Pergunte ao usuário e espere a resposta.",
        "fr": "Rien n'a encore été fait. Interrogez l'utilisateur et attendez sa réponse.",
        "zh": "尚未执行任何操作，请询问用户并等待答复。",
    },
    "tool.started_help": {
        "en": "The task is under way. Say that it is running, not that it is done.",
        "es": "La tarea está en curso. Di que está en marcha, no que está terminada.",
        "pt": "A tarefa está em andamento. Diga que está em curso, não que está concluída.",
        "fr": "La tâche est en cours. Dites qu'elle est lancée, pas qu'elle est terminée.",
        "zh": "任务正在进行，请说明正在执行，而不是已经完成。",
    },
    "tool.done_detail": {
        "en": "{action} finished.",
        "es": "{action} terminado.",
        "pt": "{action} concluída.",
        "fr": "{action} terminée.",
        "zh": "{action}已完成。",
    },

    # -- weather
    "weather.report": {
        "en": "Weather report",
        "es": "Informe del clima",
        "pt": "Boletim do tempo",
        "fr": "Bulletin météo",
        "zh": "天气预报",
    },
    "weather.for_city": {
        "en": "Weather for {city}",
        "es": "Clima en {city}",
        "pt": "Tempo em {city}",
        "fr": "Météo à {city}",
        "zh": "{city}的天气",
    },
    "weather.today": {
        "en": "Today",
        "es": "Hoy",
        "pt": "Hoje",
        "fr": "Aujourd'hui",
        "zh": "今天",
    },
    "weather.tomorrow": {
        "en": "Tomorrow",
        "es": "Mañana",
        "pt": "Amanhã",
        "fr": "Demain",
        "zh": "明天",
    },
    "weather.now": {
        "en": "Right now",
        "es": "Ahora",
        "pt": "Agora",
        "fr": "Maintenant",
        "zh": "现在",
    },
    "weather.temperature": {
        "en": "Temperature",
        "es": "Temperatura",
        "pt": "Temperatura",
        "fr": "Température",
        "zh": "温度",
    },
    "weather.feels_like": {
        "en": "Feels like",
        "es": "Sensación térmica",
        "pt": "Sensação térmica",
        "fr": "Ressenti comme",
        "zh": "体感温度",
    },
    "weather.humidity": {
        "en": "Humidity",
        "es": "Humedad",
        "pt": "Umidade",
        "fr": "Humidité",
        "zh": "湿度",
    },
    "weather.wind": {
        "en": "Wind",
        "es": "Viento",
        "pt": "Vento",
        "fr": "Vent",
        "zh": "风速",
    },
    "weather.precipitation": {
        "en": "Precipitation",
        "es": "Precipitación",
        "pt": "Precipitação",
        "fr": "Précipitations",
        "zh": "降水量",
    },
    "weather.reading": {
        "en": "{label}: {value}",
        "es": "{label} - {value}",
        "pt": "{label} - {value}",
        "fr": "{label} : {value}",
        "zh": "{label}：{value}",
    },

    # -- news
    "news.headlines": {
        "en": "Headlines",
        "es": "Titulares",
        "pt": "Manchetes",
        "fr": "À la une",
        "zh": "头条",
    },
    "news.national": {
        "en": "National news",
        "es": "Noticias nacionales",
        "pt": "Notícias nacionais",
        "fr": "Actualités nationales",
        "zh": "国内新闻",
    },
    "news.regional": {
        "en": "Regional news",
        "es": "Noticias regionales",
        "pt": "Notícias regionais",
        "fr": "Actualités régionales",
        "zh": "地区新闻",
    },
    "news.international": {
        "en": "International news",
        "es": "Noticias internacionales",
        "pt": "Notícias internacionais",
        "fr": "Actualités internationales",
        "zh": "国际新闻",
    },
    "news.source": {
        "en": "Source",
        "es": "Fuente",
        "pt": "Fonte",
        "fr": "Origine",
        "zh": "来源",
    },
    "news.summary": {
        "en": "Summary",
        "es": "Resumen",
        "pt": "Resumo",
        "fr": "Résumé",
        "zh": "摘要",
    },
    "news.item": {
        "en": "{title} - {source}",
        "es": "{title} — {source}",
        "pt": "{title} — {source}",
        "fr": "{title} — {source}",
        "zh": "{title}（{source}）",
    },

    # -- region and errors
    "region.set": {
        "en": "Region set to {region}.",
        "es": "Región establecida en {region}.",
        "pt": "Região definida como {region}.",
        "fr": "Région définie sur {region}.",
        "zh": "地区已设置为 {region}。",
    },
    "region.not_set": {
        "en": "No region set.",
        "es": "No hay región establecida.",
        "pt": "Nenhuma região definida.",
        "fr": "Aucune région définie.",
        "zh": "尚未设置地区。",
    },
    "region.current": {
        "en": "Current region: {region}",
        "es": "Región actual: {region}",
        "pt": "Região atual: {region}",
        "fr": "Région actuelle : {region}",
        "zh": "当前地区：{region}",
    },
    "error.detail": {
        "en": "Something went wrong: {reason}",
        "es": "Algo salió mal: {reason}",
        "pt": "Algo deu errado: {reason}",
        "fr": "Une erreur est survenue : {reason}",
        "zh": "出现问题：{reason}",
    },
}


# ── The interface ───────────────────────────────────────────────────────────
#
# The catalog above is what the ASSISTANT says. This block is what the PERSON
# sees: the panel titles, the button labels, the tooltips, the state words in
# the header and the HUD, the lines in the activity log.
#
# They live in the same catalog because they obey the same rule. A button that
# says "Retry" to somebody who cannot read it is not a button, it is a
# decoration. So every string here exists in all five languages, written by
# hand, with the same placeholders in the same places.
#
# The grouping follows the file the string belongs to, so a new button finds
# its key next to its neighbours.

_GUI_CATALOG: dict[str, dict[str, str]] = {
    # -- panel titles -------------------------------------------------------
    "panel.systems": {
        "en": "SYSTEMS",
        "es": "SISTEMAS",
        "pt": "SISTEMAS",
        "fr": "SYSTÈMES",
        "zh": "系统",
    },
    "panel.network": {
        "en": "NETWORK",
        "es": "RED",
        "pt": "REDE",
        "fr": "RÉSEAU",
        "zh": "网络",
    },
    "panel.status": {
        "en": "STATUS",
        "es": "ESTADO",
        "pt": "ESTADO",
        "fr": "ÉTAT",
        "zh": "状态",
    },
    "panel.tasks": {
        "en": "TASKS",
        "es": "TAREAS",
        "pt": "TAREFAS",
        "fr": "TÂCHES",
        "zh": "任务",
    },
    "panel.routines": {
        "en": "ROUTINES",
        "es": "RUTINAS",
        "pt": "ROTINAS",
        "fr": "RÉGIMES",
        "zh": "例行任务",
    },
    "panel.communications": {
        "en": "COMMUNICATIONS",
        "es": "COMUNICACIONES",
        "pt": "COMUNICAÇÕES",
        "fr": "COMMUNICATION",
        "zh": "通信",
    },
    "panel.command": {
        "en": "COMMAND",
        "es": "COMANDO",
        "pt": "COMANDO",
        "fr": "COMMANDE",
        "zh": "命令",
    },
    "panel.setup": {
        "en": "SETUP",
        "es": "AJUSTES",
        "pt": "CONFIGURAÇÃO",
        "fr": "PARAMÈTRES",
        "zh": "设置",
    },
    "panel.controls": {
        "en": "CONTROLS",
        "es": "CONTROLES",
        "pt": "CONTROLES",
        "fr": "CONTRÔLES",
        "zh": "控制",
    },
    "panel.briefing": {
        "en": "BRIEFING",
        "es": "INFORME",
        "pt": "RELATÓRIO",
        "fr": "RAPPORT",
        "zh": "简报",
    },
    "panel.quiz": {
        "en": "QUIZ",
        "es": "CUESTIONARIO",
        "pt": "PERGUNTAS",
        "fr": "QCM",
        "zh": "问答",
    },
    "panel.viewer": {
        "en": "VIEWER",
        "es": "VISOR",
        "pt": "VISOR",
        "fr": "MONITEUR",
        "zh": "观察窗",
    },
    "panel.quick": {
        "en": "QUICK ACCESS",
        "es": "ACCESOS RÁPIDOS",
        "pt": "ACESSOS RÁPIDOS",
        "fr": "ACCÈS RAPIDES",
        "zh": "快捷方式",
    },

    # -- the viewer ----------------------------------------------------------
    "viewer.camera": {
        "en": "CAMERA",
        "es": "CÁMARA",
        "pt": "CÂMERA",
        "fr": "CAMÉRA",
        "zh": "摄像头",
    },
    "viewer.camera_live": {
        "en": "CAMERA · LIVE",
        "es": "CÁMARA · EN VIVO",
        "pt": "CÂMERA · AO VIVO",
        "fr": "CAMÉRA · EN DIRECT",
        "zh": "摄像头 · 实时",
    },
    "viewer.camera_wait": {
        "en": "The camera is opening",
        "es": "La cámara se está abriendo",
        "pt": "A câmera está abrindo",
        "fr": "La caméra s'ouvre",
        "zh": "摄像头正在打开",
    },
    "viewer.camera_open": {
        "en": "The camera is open",
        "es": "La cámara está abierta",
        "pt": "A câmera está aberta",
        "fr": "La caméra est ouverte",
        "zh": "摄像头已打开",
    },
    "viewer.camera_stopped": {
        "en": "The camera stopped",
        "es": "La cámara se detuvo",
        "pt": "A câmera parou",
        "fr": "La caméra s'est arrêtée",
        "zh": "摄像头已停止",
    },
    "viewer.camera_busy": {
        "en": "Another app is using the camera — it is giving one picture a second",
        "es": "Otra aplicación está usando la cámara — da un cuadro por segundo",
        "pt": "Outro aplicativo está usando a câmera — dá um quadro por segundo",
        "fr": "Une autre application utilise la caméra — elle donne une image par seconde",
        "zh": "另一个应用正在使用摄像头 —— 每秒只有一帧",
    },
    "viewer.screen": {
        "en": "SCREEN",
        "es": "PANTALLA",
        "pt": "TELA",
        "fr": "ÉCRAN",
        "zh": "屏幕",
    },
    "viewer.screen_live": {
        "en": "SCREEN · LIVE",
        "es": "PANTALLA · EN VIVO",
        "pt": "TELA · AO VIVO",
        "fr": "ÉCRAN · EN DIRECT",
        "zh": "屏幕 · 实时",
    },
    "viewer.screen_wait": {
        "en": "The screen is being read",
        "es": "Se está leyendo la pantalla",
        "pt": "A tela está sendo lida",
        "fr": "L'écran est en cours de lecture",
        "zh": "正在读取屏幕",
    },
    "viewer.hands": {
        "en": "THE HANDS",
        "es": "LAS MANOS",
        "pt": "AS MÃOS",
        "fr": "LES MAINS",
        "zh": "手部",
    },
    "viewer.tracker": {
        "en": "Tracker",
        "es": "Rastreador",
        "pt": "Rastreador",
        "fr": "Suivi",
        "zh": "追踪器",
    },
    "viewer.pose": {
        "en": "Pose",
        "es": "Postura",
        "pt": "Postura",
        "fr": "Posture",
        "zh": "手势",
    },
    "viewer.action": {
        "en": "Action",
        "es": "Acción",
        "pt": "Ação",
        "fr": "Geste",
        "zh": "动作",
    },
    "viewer.hands_count": {
        "en": "Hands",
        "es": "Manos",
        "pt": "Mãos",
        "fr": "Mains",
        "zh": "手",
    },
    "viewer.tab_view": {
        "en": "THE VIEW",
        "es": "LA VISTA",
        "pt": "A VISTA",
        "fr": "LA VUE",
        "zh": "画面",
    },
    "viewer.tab_quiz": {
        "en": "QUESTIONS",
        "es": "PREGUNTAS",
        "pt": "PERGUNTAS",
        "fr": "CUESTIONARIO",
        "zh": "问答",
    },
    "gesture.legend": {
        "en": "PINCH click · PINCH twice double click · PINCH held right click · "
              "PINCH+move drag · PINCH+middle shift · PINCH+ring ctrl · "
              "PINCH+pinky alt · PINCH+three fingers middle click · "
              "TWO FINGERS scroll · THREE FINGERS zoom · "
              "OPEN PALM swipe · FIST let go · FINGER move · "
              "OPEN PALM held clutch · FIST held confirm · THUMB UP approve · "
              "THUMB DOWN reject · L SHAPE wake up · "
              "THREE FINGERS sideways switch window · OPEN PALM up maximise · "
              "OPEN PALM down minimise · TWO L's screenshot · "
              "TWO PINCHES one still select area · TWO PINCHES apart zoom · "
              "PALMS TOGETHER sleep",
        "es": "PELLIZCO clic · PELLIZCO dos veces doble clic · "
              "PELLIZCO mantenido clic derecho · PELLIZCO+arrastre arrastrar · "
              "PELLIZCO+medio mayús · PELLIZCO+anular control · "
              "PELLIZCO+meñique alt · PELLIZCO+tres dedos clic central · "
              "DOS DEDOS rueda · TRES DEDOS zoom · "
              "PALMA ABIERTA deslizar · PUÑO soltar todo · DEDO mover · "
              "PALMA ABIERTA quieta embrague · PUÑO quieto confirmar · "
              "PULGAR ARRIBA aprobar · PULGAR ABAJO rechazar · MANO EN L despertar · "
              "TRES DEDOS de lado cambiar de ventana · "
              "PALMA arriba maximizar · PALMA abajo minimizar · "
              "DOS L's captura · DOS PELLIZCOS uno quieto seleccionar área · "
              "DOS PELLIZCOS separados zoom · PALMAS JUNTAS dormir",
        "pt": "BELISCA clique · BELISCA duas vezes clique duplo · "
              "BELISCA mantido clique direito · BELISCA+arrastar arrastar · "
              "BELISCA+médio shift · BELISCA+anelar ctrl · "
              "BELISCA+mindinho alt · BELISCA+três dedos clique do meio · "
              "DOIS DEDOS rolagem · TRÊS DEDOS zoom · "
              "PALMA ABERTA deslizar · PUNHO largar tudo · DEDO mover · "
              "PALMA ABERTA quieta embrague · PUNHO quieto confirmar · "
              "POLEGAR PARA CIMA aprovar · POLEGAR PARA BAIXO rejeitar · "
              "MÃO EM L acordar · TRÊS DEDOS de lado trocar de janela · "
              "PALMA para cima maximizar · PALMA para baixo minimizar · "
              "DOIS L's captura · DOIS BELISCAS um quieto selecionar área · "
              "DOIS BELISCAS separados zoom · PALMAS JUNTAS dormir",
        "fr": "PINCE clic · PINCE deux fois double clic · PINCE tenu clic droit · "
              "PINCE+glisser glisser · PINCE+majeur shift · PINCE+annulaire ctrl · "
              "PINCE+auriculaire alt · PINCE+trois doigts clic du milieu · "
              "DEUX DOIGTS molette · TROIS DOIGTS zoom · "
              "PAUME OUVERTE balayer · POING tout lâcher · DOIGT déplacer · "
              "PAUME OUVERTE immobile embrayage · POING immobile confirmer · "
              "POUCE EN HAUT approuver · POUCE VERS LE BAS refuser · "
              "MAIN EN L réveiller · TROIS DOIGTS sur le côté changer de fenêtre · "
              "PAUME vers le haut agrandir · PAUME vers le bas réduire · "
              "DEUX L capture · DEUX PINCES un immobile sélection de zone · "
              "DEUX PINCES écartés zoom · PAUMES ENSEMBLE dormir",
        "zh": "捏合 点击 · 捏合两次 双击 · 长捏合 右键 · "
              "捏合加移动 拖动 · 捏合加中指 Shift · 捏合加无名指 Ctrl · "
              "捏合加小指 Alt · 捏合加三指 中键 · "
              "两指 滚轮 · 三指 缩放 · "
              "张开手掌 滑动 · 握拳 松开 · 单指 移动 · "
              "张开手掌不动 离合 · 握拳不动 确认 · "
              "拇指向上 批准 · 拇指向下 拒绝 · L形手势 唤醒 · "
              "三指横移 切换窗口 · 手掌上推 最大化 · 手掌下推 最小化 · "
              "双手L形 截图 · 双手捏合一个不动 选择区域 · 双手捏合分开 缩放 · "
              "双掌合拢 休眠",
    },
    # ── what the hands are saying, in the user's language ────────────────────
    #
    # The strip under the pictures says what the tracker read and what it
    # means. It used to say it in English whatever the language of the
    # interface, which is exactly the kind of thing that makes a person
    # think the machine does not understand them.
    "gesture.pose.pinch": {
        "en": "PINCH", "es": "PELLIZCO", "pt": "BELISCA", "fr": "PINCE",
        "zh": "捏合",
    },
    "gesture.pose.point": {
        "en": "FINGER", "es": "DEDO", "pt": "DEDO", "fr": "DOIGT", "zh": "单指",
    },
    "gesture.pose.two": {
        "en": "TWO FINGERS", "es": "DOS DEDOS", "pt": "DOIS DEDOS",
        "fr": "DEUX DOIGTS", "zh": "两指",
    },
    "gesture.pose.three": {
        "en": "THREE FINGERS", "es": "TRES DEDOS", "pt": "TRÊS DEDOS",
        "fr": "TROIS DOIGTS", "zh": "三指",
    },
    "gesture.pose.open": {
        "en": "OPEN PALM", "es": "PALMA ABIERTA", "pt": "PALMA ABERTA",
        "fr": "PAUME OUVERTE", "zh": "张开手掌",
    },
    "gesture.pose.fist": {
        "en": "FIST", "es": "PUÑO", "pt": "PUNHO", "fr": "POING", "zh": "握拳",
    },
    "gesture.pose.hand": {
        "en": "HAND", "es": "MANO", "pt": "MÃO", "fr": "MAIN", "zh": "手",
    },
    "gesture.pose.thumb_up": {
        "en": "THUMB UP", "es": "PULGAR ARRIBA", "pt": "POLEGAR PARA CIMA",
        "fr": "POUCE EN HAUT", "zh": "拇指向上",
    },
    "gesture.pose.thumb_down": {
        "en": "THUMB DOWN", "es": "PULGAR ABAJO", "pt": "POLEGAR PARA BAIXO",
        "fr": "POUCE VERS LE BAS", "zh": "拇指向下",
    },
    "gesture.pose.l": {
        "en": "L SHAPE", "es": "MANO EN L", "pt": "MÃO EM L", "fr": "MAIN EN L",
        "zh": "L形手势",
    },
    "gesture.action.move": {
        "en": "move", "es": "mover", "pt": "mover", "fr": "déplacer", "zh": "移动",
    },
    "gesture.action.click": {
        "en": "click", "es": "clic", "pt": "clique", "fr": "clic", "zh": "点击",
    },
    "gesture.action.double click": {
        "en": "double click", "es": "doble clic", "pt": "clique duplo",
        "fr": "double clic", "zh": "双击",
    },
    "gesture.action.right click": {
        "en": "right click", "es": "clic derecho", "pt": "clique direito",
        "fr": "clic droit", "zh": "右键",
    },
    "gesture.action.middle click": {
        "en": "middle click", "es": "clic central", "pt": "clique do meio",
        "fr": "clic du milieu", "zh": "中键",
    },
    "gesture.action.drag": {
        "en": "drag", "es": "arrastrar", "pt": "arrastar", "fr": "glisser", "zh": "拖动",
    },
    "gesture.action.press": {
        "en": "press", "es": "presionar", "pt": "premir", "fr": "appuyer", "zh": "按下",
    },
    "gesture.action.scroll": {
        "en": "scroll", "es": "rueda", "pt": "rolagem", "fr": "molette", "zh": "滚轮",
    },
    "gesture.action.zoom": {
        "en": "zoom", "es": "ampliación", "pt": "ampliação",
        "fr": "agrandissement", "zh": "缩放",
    },
    "gesture.action.zoom in": {
        "en": "zoom in", "es": "acercar", "pt": "aproximar", "fr": "agrandir",
        "zh": "放大",
    },
    "gesture.action.zoom out": {
        "en": "zoom out", "es": "alejar", "pt": "afastar", "fr": "rétrécir",
        "zh": "缩小",
    },
    "gesture.action.swipe": {
        "en": "swipe", "es": "deslizar", "pt": "deslizar", "fr": "balayer", "zh": "滑动",
    },
    "gesture.action.rest": {
        "en": "let go", "es": "soltar todo", "pt": "largar tudo", "fr": "tout lâcher",
        "zh": "松开",
    },
    "gesture.action.clutch": {
        "en": "clutch", "es": "embrague", "pt": "embrague", "fr": "embrayage",
        "zh": "离合",
    },
    "gesture.action.confirm": {
        "en": "confirm", "es": "confirmar", "pt": "confirmar", "fr": "confirmer",
        "zh": "确认",
    },
    "gesture.action.approve": {
        "en": "approve", "es": "aprobar", "pt": "aprovar", "fr": "approuver",
        "zh": "批准",
    },
    "gesture.action.reject": {
        "en": "reject", "es": "rechazar", "pt": "rejeitar", "fr": "refuser",
        "zh": "拒绝",
    },
    "gesture.action.switch window": {
        "en": "switch window", "es": "cambiar de ventana",
        "pt": "trocar de janela", "fr": "changer de fenêtre", "zh": "切换窗口",
    },
    "gesture.action.maximize": {
        "en": "maximize", "es": "maximizar", "pt": "maximizar", "fr": "agrandir",
        "zh": "最大化",
    },
    "gesture.action.minimize": {
        "en": "minimize", "es": "minimizar", "pt": "minimizar", "fr": "réduire",
        "zh": "最小化",
    },
    "gesture.action.dictation": {
        "en": "dictation", "es": "dictado", "pt": "ditado", "fr": "dictee",
        "zh": "听写",
    },
    "gesture.action.screenshot": {
        "en": "screenshot", "es": "captura", "pt": "captura", "fr": "capture",
        "zh": "截图",
    },
    "gesture.action.area": {
        "en": "select area", "es": "seleccionar área", "pt": "selecionar área",
        "fr": "sélection de zone", "zh": "选择区域",
    },
    "gesture.action.sleep": {
        "en": "sleep", "es": "dormir", "pt": "dormir", "fr": "dormir",
        "zh": "休眠",
    },

    # -- what the hand says about what it just did -----------------------------
    "gesture.confirmed": {
        "en": "the hand approved it", "es": "la mano lo aprobó",
        "pt": "a mão aprovou", "fr": "la main l'a approuvé", "zh": "手已批准",
    },
    "gesture.rejected": {
        "en": "the hand rejected it", "es": "la mano lo rechazó",
        "pt": "a mão rejeitou", "fr": "la main l'a refusé", "zh": "手已拒绝",
    },
    "gesture.asleep": {
        "en": "T.E.E.M.O. went to sleep", "es": "T.E.E.M.O. se durmió",
        "pt": "T.E.E.M.O. adormeceu", "fr": "T.E.E.M.O. s'est endormi",
        "zh": "T.E.E.M.O. 睡着了",
    },

    # -- the groups of the controls drawer ------------------------------------
    "group.voice": {
        "en": "VOICE",
        "es": "VOZ",
        "pt": "VOZ",
        "fr": "VOIX",
        "zh": "语音",
    },
    "group.language": {
        "en": "LANGUAGE",
        "es": "IDIOMA",
        "pt": "IDIOMA",
        "fr": "LANGUE",
        "zh": "语言",
    },
    "group.look": {
        "en": "WINDOW",
        "es": "VENTANA",
        "pt": "JANELA",
        "fr": "FENÊTRE",
        "zh": "窗口",
    },
    "group.hand": {
        "en": "HAND",
        "es": "MANO",
        "pt": "MÃO",
        "fr": "MAIN",
        "zh": "手",
    },
    "group.session": {
        "en": "SESSION",
        "es": "SESIÓN",
        "pt": "SESSÃO",
        "fr": "SÉANCE",
        "zh": "会话",
    },
    "group.system": {
        "en": "SYSTEM",
        "es": "SISTEMA",
        "pt": "SISTEMA",
        "fr": "SYSTÈME",
        "zh": "系统",
    },
    "group.tools": {
        "en": "TOOLS",
        "es": "HERRAMIENTAS",
        "pt": "FERRAMENTAS",
        "fr": "OUTILS",
        "zh": "工具",
    },

    # -- qué es cada grupo del cajón -----------------------------------------
    #
    # El dueño preguntó qué era cada cosa: qué es el mazo, qué es la
    # ventana flotante, qué hace "pantalla completa" y qué tiene que ver
    # "sistema" con "ventana". Una línea por grupo y una por control
    # contestan esas preguntas sin que nadie tenga que adivinar.
    "drawer.voice.explain": {
        "en": "How it listens and how it answers.",
        "es": "Cómo escucha y cómo contesta.",
        "pt": "Como escuta e como responde.",
        "fr": "Comment il écoute et comment il répond.",
        "zh": "它如何听、如何回答。",
    },
    "drawer.language.explain": {
        "en": "The language it speaks and the place it believes you are in.",
        "es": "El idioma que habla y el lugar donde cree que estás.",
        "pt": "O idioma que fala e o lugar onde acredita que você está.",
        "fr": "La langue qu'il parle et l'endroit où il croit que vous êtes.",
        "zh": "它使用的语言，以及它认为你所在的地方。",
    },
    "drawer.look.explain": {
        "en": "The shape of the window: the whole panel, the core in a corner, "
              "or only the icon in the tray.",
        "es": "La forma de la ventana: el panel entero, el núcleo en una esquina "
              "o sólo el icono en la bandeja.",
        "pt": "A forma da janela: o painel inteiro, o núcleo num canto ou só "
              "o ícone na bandeja.",
        "fr": "La forme de la fenêtre : le panneau entier, le noyau dans un coin, "
              "ou seulement l'icône dans la zone de notification.",
        "zh": "窗口的形态：完整面板、角落中的核心，或仅托盘图标。",
    },
    "drawer.window_shapes.explain": {
        "en": "Maximized keeps the taskbar, full screen does not, and minimize "
              "leaves only the icon.",
        "es": "Maximizada conserva la barra de tareas, a pantalla completa no, "
              "y minimizada deja sólo el icono.",
        "pt": "Maximizada mantém a barra de tarefas, em ecrã inteiro não, e "
              "minimizada deixa só o ícone.",
        "fr": "Maximisée garde la barre des tâches, en plein écran non, et "
              "réduite ne laisse que l'icône.",
        "zh": "最大化保留任务栏，全屏不保留，最小化只剩图标。",
    },
    "drawer.hand.explain": {
        "en": "The hand as a mouse: the camera follows it and the pinch is the button.",
        "es": "La mano como ratón: la cámara la sigue y el pellizco es el botón.",
        "pt": "A mão como rato: a câmara segue-a e a belisca é o botão.",
        "fr": "La main comme souris : la caméra la suit et le pincement est le bouton.",
        "zh": "手就是鼠标：摄像头跟随它，捏合就是按键。",
    },
    "drawer.session.explain": {
        "en": "The deck of cards, the panel that floats over another program, "
              "and which tools the model is offered.",
        "es": "El mazo de tarjetas, el panel que flota sobre otro programa y "
              "qué herramientas se le ofrecen al modelo.",
        "pt": "O baralho de cartas, o painel que flutua sobre outro programa e "
              "quais ferramentas são oferecidas ao modelo.",
        "fr": "Le deck de cartes, le panneau qui flotte au-dessus d'un autre "
              "programme et les outils proposés au modèle.",
        "zh": "卡片堆、浮在其他程序之上的面板，以及提供给模型的工具。",
    },
    "drawer.deck.explain": {
        "en": "The deck: the news and the answers, as cards you open and close.",
        "es": "El mazo: las noticias y las respuestas, en tarjetas que abres y cierras.",
        "pt": "O baralho: as notícias e as respostas, em cartões que abre e fecha.",
        "fr": "Le deck : les nouvelles et les réponses, en cartes que vous "
              "ouvrez et fermez.",
        "zh": "卡片堆：新闻和回答都在可开合的卡片里。",
    },
    "drawer.floating.explain": {
        "en": "The floating panel: a window of its own that stays on top of "
              "the other programs.",
        "es": "El panel flotante: una ventana propia que se queda encima de "
              "los demás programas.",
        "pt": "O painel flutuante: uma janela própria que fica por cima dos "
              "outros programas.",
        "fr": "Le panneau flottant : une fenêtre à part qui reste au-dessus "
              "des autres programmes.",
        "zh": "浮动面板：一个独立窗口，始终停在其他程序之上。",
    },
    "drawer.system.explain": {
        "en": "Which program plays the music, and what is still half done.",
        "es": "Qué programa reproduce la música y qué está a medio hacer.",
        "pt": "Que programa toca a música e o que está meio feito.",
        "fr": "Quel programme joue la musique, et ce qui est encore à moitié fait.",
        "zh": "用哪个程序播放音乐，以及哪些功能还没做完。",
    },
    "drawer.tools.explain": {
        "en": "Each of these opens a window of its own.",
        "es": "Cada una de estas abre una ventana propia.",
        "pt": "Cada uma destas abre uma janela própria.",
        "fr": "Chacune de celles-ci ouvre une fenêtre à part.",
        "zh": "每一项都会打开一个独立的窗口。",
    },
    "drawer.tool.remote": {
        "en": "Shows the QR code that pairs the phone with T.E.E.M.O.",
        "es": "Muestra el código QR que empareja el móvil con T.E.E.M.O.",
        "pt": "Mostra o código QR que emparelha o telemóvel com o T.E.E.M.O.",
        "fr": "Affiche le code QR qui associe le téléphone à T.E.E.M.O.",
        "zh": "显示把手机与 T.E.E.M.O. 配对的二维码。",
    },
    "drawer.tool.shortcut": {
        "en": "Writes a shortcut on the desktop.",
        "es": "Escribe un acceso directo en el escritorio.",
        "pt": "Escreve um atalho no ambiente de trabalho.",
        "fr": "Écrit un raccourci sur le bureau.",
        "zh": "在桌面上创建快捷方式。",
    },
    "drawer.tool.autostart": {
        "en": "Starts with Windows, before anybody asks.",
        "es": "Arranca con Windows, antes de que nadie se lo pida.",
        "pt": "Arranca com o Windows, antes de alguém pedir.",
        "fr": "Démarre avec Windows, avant que quiconque le demande.",
        "zh": "随 Windows 自动启动，无需询问。",
    },
    "drawer.tool.customise": {
        "en": "Changes the name, the colour and the voice.",
        "es": "Cambia el nombre, el color y la voz.",
        "pt": "Muda o nome, a cor e a voz.",
        "fr": "Change le nom, la couleur et la voix.",
        "zh": "更改名称、颜色和声音。",
    },
    "drawer.tool.audio": {
        "en": "Chooses the microphone and the speakers.",
        "es": "Elige el micrófono y los altavoces.",
        "pt": "Escolhe o microfone e as colunas.",
        "fr": "Choisit le microphone et les haut-parleurs.",
        "zh": "选择麦克风和扬声器。",
    },
    "drawer.tool.memory": {
        "en": "Everything the assistant has remembered about you.",
        "es": "Todo lo que el asistente ha guardado de ti.",
        "pt": "Tudo o que o assistente guardou sobre si.",
        "fr": "Tout ce que l'assistant a retenu de vous.",
        "zh": "助手记住的关于你的一切。",
    },
    "drawer.tool.plugins": {
        "en": "The plugins: Gmail, the calendar and Spotify.",
        "es": "Los complementos: Gmail, el calendario y Spotify.",
        "pt": "Os plug-ins: Gmail, o calendário e o Spotify.",
        "fr": "Les modules : Gmail, l'agenda et Spotify.",
        "zh": "插件：Gmail、日历和 Spotify。",
    },
    "drawer.tool.plugin_settings": {
        "en": "The settings of each plugin, one by one.",
        "es": "Los ajustes de cada complemento, uno por uno.",
        "pt": "As definições de cada plug-in, uma a uma.",
        "fr": "Les réglages de chaque module, un par un.",
        "zh": "每个插件的设置，逐个设置。",
    },
    "drawer.tool.providers": {
        "en": "The models and the keys it thinks with.",
        "es": "Los modelos y las claves con los que piensa.",
        "pt": "Os modelos e as chaves com que pensa.",
        "fr": "Les modèles et les clés avec lesquels il pense.",
        "zh": "它思考所用的模型和密钥。",
    },
    "drawer.copy": {
        "en": "COPY",
        "es": "COPIAR",
        "pt": "COPIAR",
        "fr": "COPIER",
        "zh": "复制",
    },
    "drawer.copy_tip": {
        "en": "Copies the open group as text, to paste it into the chat.",
        "es": "Copia el grupo abierto como texto, para pegarlo en el chat.",
        "pt": "Copia o grupo aberto como texto, para o colar na conversa.",
        "fr": "Copie le groupe ouvert sous forme de texte, pour le coller "
              "dans la conversation.",
        "zh": "把当前分组复制成文本，方便粘贴到对话里。",
    },
    "drawer.copied": {
        "en": "Copied to the clipboard.",
        "es": "Copiado al portapapeles.",
        "pt": "Copiado para a área de transferência.",
        "fr": "Copié dans le presse-papiers.",
        "zh": "已复制到剪贴板。",
    },
    "gesture.backend.none": {
        "en": "no tracker",
        "es": "sin rastreador",
        "pt": "sem rastreador",
        "fr": "aucun détecteur",
        "zh": "无追踪器",
    },
    "hand.left": {
        "en": "LEFT", "es": "IZQUIERDA", "pt": "ESQUERDA", "fr": "GAUCHE",
        "zh": "左手",
    },
    "hand.right": {
        "en": "RIGHT", "es": "DERECHA", "pt": "DIREITA", "fr": "DROITE",
        "zh": "右手",
    },
    "gesture.backend.mediapipe": {
        "en": "MediaPipe",
        "es": "MediaPipe",
        "pt": "MediaPipe",
        "fr": "MediaPipe",
        "zh": "MediaPipe",
    },
    "gesture.backend.mediapipe-legacy": {
        "en": "MediaPipe (older version)",
        "es": "MediaPipe (versión antigua)",
        "pt": "MediaPipe (versão antiga)",
        "fr": "MediaPipe (version plus ancienne)",
        "zh": "MediaPipe（旧版）",
    },
    "gesture.backend.opencv": {
        "en": "OpenCV (approximate)",
        "es": "OpenCV (aproximado)",
        "pt": "OpenCV (aproximado)",
        "fr": "OpenCV (approximatif)",
        "zh": "OpenCV（近似）",
    },

    # -- the window ----------------------------------------------------------
    "window.maximize": {
        "en": "MAXIMIZE",
        "es": "MAXIMIZAR",
        "pt": "MAXIMIZAR",
        "fr": "AGRANDIR",
        "zh": "最大化",
    },
    "window.restore": {
        "en": "WINDOW",
        "es": "VENTANA",
        "pt": "JANELA",
        "fr": "FENÊTRE",
        "zh": "窗口",
    },
    "window.minimize": {
        "en": "MINIMIZE",
        "es": "MINIMIZAR",
        "pt": "MINIMIZAR",
        "fr": "RÉDUIRE",
        "zh": "最小化",
    },
    "window.leave_fullscreen": {
        "en": "LEAVE FULLSCREEN",
        "es": "SALIR DE PANTALLA COMPLETA",
        "pt": "SAIR DA TELA CHEIA",
        "fr": "QUITTER LE PLEIN ÉCRAN",
        "zh": "退出全屏",
    },
    "window.window_modes": {
        "en": "WINDOW",
        "es": "VENTANA",
        "pt": "JANELA",
        "fr": "FENÊTRE",
        "zh": "窗口",
    },
    "window.open_viewer": {
        "en": "Open the viewer",
        "es": "Abrir el visor",
        "pt": "Abrir o visor",
        "fr": "Ouvrir le moniteur",
        "zh": "打开观察窗",
    },
    "window.open_viewer_tip": {
        "en": "Camera on the left, the screen on the right, the hands below",
        "es": "Cámara a la izquierda, la pantalla a la derecha, las manos abajo",
        "pt": "Câmera à esquerda, a tela à direita, as mãos abaixo",
        "fr": "Caméra à gauche, l'écran à droite, les mains en bas",
        "zh": "左摄像头，右屏幕，下为手部",
    },
    "window.quick_access": {
        "en": "QUICK ACCESS",
        "es": "ACCESOS RÁPIDOS",
        "pt": "ACESSOS RÁPIDOS",
        "fr": "ACCÈS RAPIDES",
        "zh": "快捷方式",
    },
    "tab.conversation": {
        "en": "TALK",
        "es": "CHARLA",
        "pt": "CONVERSA",
        "fr": "DISCUSSION",
        "zh": "对话",
    },
    "tab.commands": {
        "en": "SAY IT",
        "es": "DILO",
        "pt": "DIGA",
        "fr": "DITES",
        "zh": "说",
    },
    "tab.tasks": {
        "en": "WORK",
        "es": "TAREAS",
        "pt": "TAREFAS",
        "fr": "TÂCHES",
        "zh": "任务",
    },

    # -- small labels -------------------------------------------------------
    # "CPU" and "GPU" are not in here: they are hardware acronyms and read the
    # same in every language, so translating them would only make them wrong.
    "label.memory": {
        "en": "MEMORY",
        "es": "MEMORIA",
        "pt": "MEMÓRIA",
        "fr": "MÉMOIRE",
        "zh": "内存",
    },
    "label.temp": {
        "en": "TEMP",
        "es": "TEMP.",
        "pt": "TEMP.",
        "fr": "TEMP.",
        "zh": "温度",
    },
    "label.uptime": {
        "en": "UPTIME",
        "es": "TIEMPO ACTIVO",
        "pt": "TEMPO ATIVO",
        "fr": "TEMPS DE FONCTIONNEMENT",
        "zh": "运行时间",
    },
    "label.processes": {
        "en": "PROCESSES",
        "es": "PROCESOS",
        "pt": "PROCESSOS",
        "fr": "PROCESSUS",
        "zh": "进程",
    },
    "label.platform": {
        "en": "PLATFORM",
        "es": "PLATAFORMA",
        "pt": "PLATAFORMA",
        "fr": "PLATEFORME",
        "zh": "平台",
    },
    "label.engine": {
        "en": "ENGINE",
        "es": "MOTOR",
        "pt": "MOTOR",
        "fr": "MOTEUR",
        "zh": "引擎",
    },
    "label.live": {
        "en": "LIVE",
        "es": "EN VIVO",
        "pt": "AO VIVO",
        "fr": "EN DIRECT",
        "zh": "实时",
    },
    "label.log": {
        "en": "LOG",
        "es": "REGISTRO",
        "pt": "REGISTRO",
        "fr": "JOURNAL",
        "zh": "日志",
    },
    "label.active": {
        "en": "RUNNING",
        "es": "EN MARCHA",
        "pt": "EM ANDAMENTO",
        "fr": "EN COURS",
        "zh": "进行中",
    },
    "label.enabled": {
        "en": "ON",
        "es": "ACTIVAS",
        "pt": "LIGADAS",
        "fr": "ACTIVÉES",
        "zh": "已启用",
    },
    "label.input": {
        "en": "INPUT",
        "es": "ENTRADA",
        "pt": "ENTRADA",
        "fr": "ENTRÉE",
        "zh": "输入",
    },

    # -- the window ---------------------------------------------------------
    "window.personal_ai_system": {
        "en": "PERSONAL AI SYSTEM",
        "es": "SISTEMA DE IA PERSONAL",
        "pt": "SISTEMA DE IA PESSOAL",
        "fr": "SYSTÈME D'IA PERSONNEL",
        "zh": "个人 AI 系统",
    },
    "window.subtitle_default": {
        "en": "Personal AI Assistant",
        "es": "Asistente de IA personal",
        "pt": "Assistente de IA pessoal",
        "fr": "Assistant IA personnel",
        "zh": "个人 AI 助手",
    },
    "window.setup_drawer_tip": {
        "en": "Setup — things you set once",
        "es": "Ajustes — lo que configuras una vez",
        "pt": "Configuração — o que se ajusta uma vez",
        "fr": "Paramètres — ce que vous réglez une fois",
        "zh": "设置——只需设置一次的内容",
    },
    "window.controls_drawer_tip": {
        "en": "Controls — the everyday switches",
        "es": "Controles — los interruptores de cada día",
        "pt": "Controles — os interruptores do dia a dia",
        "fr": "Contrôles — les interrupteurs de tous les jours",
        "zh": "控制——日常用的开关",
    },
    "window.deck_tip": {
        "en": "The deck — everything the assistant said this session  [Ctrl+Alt+E]",
        "es": "El mazo — todo lo que dijo el asistente en esta sesión  [Ctrl+Alt+E]",
        "pt": "O baralho — tudo o que o assistente disse nesta sessão  [Ctrl+Alt+E]",
        "fr": "La pioche — tout ce que l'assistant a dit cette session  [Ctrl+Alt+E]",
        "zh": "牌堆——本次会话助手说过的一切  [Ctrl+Alt+E]",
    },
    "window.briefing_tip": {
        "en": "The morning briefing — the hour, the weather and your agenda",
        "es": "El resumen matutino — la hora, el clima y tu agenda",
        "pt": "O resumo da manhã — a hora, o clima e sua agenda",
        "fr": "Le point du matin — l'heure, la météo et votre agenda",
        "zh": "晨间简报——时间、天气和日程",
    },
    "window.briefing_not_ready": {
        "en": "the briefing is not ready yet — the assistant is still starting",
        "es": "el resumen todavía no está listo — el asistente está arrancando",
        "pt": "o resumo ainda não está pronto — o assistente está iniciando",
        "fr": "le point n'est pas encore prêt — l'assistant démarre",
        "zh": "简报尚未准备好——助手正在启动",
    },
    "window.boot_done": {
        "en": "the ignition is over — everything is up",
        "es": "el arranque ha terminado — todo está en pie",
        "pt": "a ignição acabou — tudo está de pé",
        "fr": "le démarrage est terminé — tout est en place",
        "zh": "启动完成——一切就绪",
    },
    "window.boot_replayed": {
        "en": "the ignition is playing again",
        "es": "el arranque se reproduce otra vez",
        "pt": "a ignição está a ser reproduzida outra vez",
        "fr": "le démarrage se rejoue",
        "zh": "启动再次播放",
    },
    "window.boot_starting": {
        "en": "STARTING",
        "es": "ARRANCANDO",
        "pt": "A INICIAR",
        "fr": "DÉMARRAGE",
        "zh": "启动中",
    },
    "controls.deck": {
        "en": "DECK — the session on one screen",
        "es": "MAZO — la sesión en una pantalla",
        "pt": "BARALHO — a sessão num ecrã",
        "fr": "PIOCHE — la session sur un écran",
        "zh": "牌堆——会话尽在一屏",
    },
    "controls.deck_tip": {
        "en": "Every answer the assistant gave, one card each. "
              "E folds the card, X closes the deck, [ and ] walk through it.",
        "es": "Cada respuesta del asistente, una tarjeta. "
              "E pliega la tarjeta, X cierra el mazo, [ y ] recorren el mazo.",
        "pt": "Cada resposta do assistente, um cartão. "
              "E dobra o cartão, X fecha o baralho, [ e ] percorrem o baralho.",
        "fr": "Chaque réponse de l'assistant, une carte. "
              "E plie la carte, X ferme la pioche, [ et ] parcourent la pioche.",
        "zh": "助手的每一条回答各占一张卡片。"
              "E 折叠卡片，X 收起牌堆，[ 和 ] 翻阅牌堆。",
    },
    "controls.floating": {
        "en": "FLOATING PANEL — always on top",
        "es": "PANEL FLOTANTE — siempre encima",
        "pt": "PAINEL FLUTUANTE — sempre por cima",
        "fr": "PANNEAU FLOTTANT — toujours au-dessus",
        "zh": "悬浮面板——始终置顶",
    },
    "controls.floating_tip": {
        "en": "A small panel over every other window, with the state and "
              "the last answer. Drag it with the mouse or with the hand.",
        "es": "Un panel pequeño sobre cualquier otra ventana, con el estado "
              "y la última respuesta. Arrástralo con el ratón o con la mano.",
        "pt": "Um pequeno painel sobre qualquer outra janela, com o estado "
              "e a última resposta. Arraste-o com o rato ou com a mão.",
        "fr": "Un petit panneau au-dessus de toute autre fenêtre, avec l'état "
              "et la dernière réponse. Déplacez-le à la souris ou à la main.",
        "zh": "一个浮在其他窗口之上的小面板，显示状态和最后一条回答。"
              "可以用鼠标或用手拖动它。",
    },
    "controls.replay_boot": {
        "en": "IGNITION — watch it again",
        "es": "ARRANQUE — velo otra vez",
        "pt": "IGNIÇÃO — veja outra vez",
        "fr": "DÉMARRAGE — rejouez-le",
        "zh": "启动——再看一次",
    },
    "controls.replay_boot_tip": {
        "en": "The screen that comes up before the assistant is ready, with "
              "the real check of every subsystem.",
        "es": "La pantalla que sale antes de que el asistente esté listo, con "
              "la comprobación real de cada subsistema.",
        "pt": "O ecrã que aparece antes de o assistente estar pronto, com a "
              "verificação real de cada subsistema.",
        "fr": "L'écran qui s'affiche avant que l'assistant soit prêt, avec la "
              "vérification réelle de chaque sous-système.",
        "zh": "助手就绪前出现的画面，对每个子系统做真实检查。",
    },
    "window.fullscreen_tip": {
        "en": "Fullscreen  [F11]",
        "es": "Pantalla completa  [F11]",
        "pt": "Ecrã inteiro  [F11]",
        "fr": "Plein écran  [F11]",
        "zh": "全屏  [F11]",
    },
    "window.mic_active": {
        "en": "MIC ACTIVE",
        "es": "MICRÓFONO ACTIVO",
        "pt": "MICROFONE ATIVO",
        "fr": "MICRO ACTIF",
        "zh": "麦克风已开",
    },
    "window.mic_muted": {
        "en": "MIC MUTED",
        "es": "MICRÓFONO EN SILENCIO",
        "pt": "MICROFONE MUDO",
        "fr": "MICRO COUPÉ",
        "zh": "麦克风已静音",
    },
    "window.mute_tip": {
        "en": "Mute or unmute the microphone  [F4]",
        "es": "Silenciar o activar el micrófono  [F4]",
        "pt": "Silenciar ou ativar o microfone  [F4]",
        "fr": "Couper ou réactiver le micro  [F4]",
        "zh": "静音或取消静音麦克风  [F4]",
    },
    "window.interrupt": {
        "en": "INTERRUPT",
        "es": "INTERRUMPIR",
        "pt": "INTERROMPER",
        "fr": "INTERROMPRE",
        "zh": "打断",
    },
    "window.interrupt_tip": {
        "en": "Stop the assistant mid-sentence  [ESC]",
        "es": "Detener al asistente a mitad de frase  [ESC]",
        "pt": "Parar o assistente no meio da frase  [ESC]",
        "fr": "Arrêter l'assistant en cours de phrase  [ESC]",
        "zh": "打断助手说话  [ESC]",
    },
    "window.type_command": {
        "en": "Type a command or question…",
        "es": "Escribe una orden o una pregunta…",
        "pt": "Escreva um comando ou uma pergunta…",
        "fr": "Tapez une commande ou une question…",
        "zh": "输入命令或问题…",
    },
    "window.send_tip": {
        "en": "Send  [Enter]",
        "es": "Enviar  [Enter]",
        "pt": "Enviar  [Enter]",
        "fr": "Envoyer  [Enter]",
        "zh": "发送  [Enter]",
    },
    "window.dismiss": {
        "en": "Dismiss",
        "es": "Descartar",
        "pt": "Dispensar",
        "fr": "Ignorer",
        "zh": "关闭",
    },
    "window.no_file_loaded": {
        "en": "No file loaded",
        "es": "Ningún archivo cargado",
        "pt": "Nenhum ficheiro carregado",
        "fr": "Aucun fichier chargé",
        "zh": "未加载文件",
    },
    "window.footer_shortcuts": {
        "en": "F4 MUTE   ·   F11 FULLSCREEN   ·   ESC INTERRUPT   ·   BY FATIHMAKES",
        "es": "F4 SILENCIO   ·   F11 PANTALLA COMPLETA   ·   ESC INTERRUMPIR   ·   BY FATIHMAKES",
        "pt": "F4 SILENCIAR   ·   F11 ECRÃ INTEIRO   ·   ESC INTERROMPER   ·   BY FATIHMAKES",
        "fr": "F4 COUPER LE MICRO   ·   F11 PLEIN ÉCRAN   ·   ESC INTERROMPRE   ·   BY FATIHMAKES",
        "zh": "F4 静音   ·   F11 全屏   ·   ESC 打断   ·   BY FATIHMAKES",
    },
    "window.camera_feed": {
        "en": "◈  CAMERA FEED",
        "es": "◈  CÁMARA EN VIVO",
        "pt": "◈  CÂMERA AO VIVO",
        "fr": "◈  FLUX VIDÉO",
        "zh": "◈  摄像头画面",
    },
    "window.close": {
        "en": "✕  CLOSE",
        "es": "✕  CERRAR",
        "pt": "✕  FECHAR",
        "fr": "✕  FERMER",
        "zh": "✕  关闭",
    },
    "window.video": {
        "en": "▶  VIDEO",
        "es": "▶  VÍDEO",
        "pt": "▶  VÍDEO",
        "fr": "▶  VIDÉO",
        "zh": "▶  视频",
    },
    "window.video_label": {
        "en": "VIDEO",
        "es": "VÍDEO",
        "pt": "VÍDEO",
        "fr": "VIDÉO",
        "zh": "视频",
    },
    "window.picture_label": {
        "en": "PICTURE",
        "es": "IMAGEN",
        "pt": "IMAGEM",
        "fr": "IMAGE",
        "zh": "图片",
    },
    "window.picture": {
        "en": "◧  PICTURE",
        "es": "◧  IMAGEN",
        "pt": "◧  IMAGEM",
        "fr": "◧  IMAGE",
        "zh": "◧  图片",
    },
    "window.sound_off": {
        "en": "🔇  SOUND OFF",
        "es": "🔇  SONIDO APAGADO",
        "pt": "🔇  SOM DESLIGADO",
        "fr": "🔇  SON COUPÉ",
        "zh": "🔇  已静音",
    },
    "window.sound_on": {
        "en": "🔊  SOUND ON",
        "es": "🔊  SONIDO ENCENDIDO",
        "pt": "🔊  SOM LIGADO",
        "fr": "🔊  SON ACTIVÉ",
        "zh": "🔊  有声",
    },
    "window.status_mic_live": {
        "en": "MIC · LIVE",
        "es": "MIC · EN VIVO",
        "pt": "MIC · AO VIVO",
        "fr": "MIC · EN DIRECT",
        "zh": "麦克风 · 实时",
    },
    "window.status_mic_muted": {
        "en": "MIC · MUTED",
        "es": "MIC · SILENCIADO",
        "pt": "MIC · MUDO",
        "fr": "MIC · COUPÉ",
        "zh": "麦克风 · 已静音",
    },
    "window.status_engine": {
        "en": "ENGINE · {engine}",
        "es": "MOTOR · {engine}",
        "pt": "MOTOR · {engine}",
        "fr": "MOTEUR · {engine}",
        "zh": "引擎 · {engine}",
    },
    "window.status_protocol": {
        "en": "PROTOCOL · {protocol}",
        "es": "PROTOCOLO · {protocol}",
        "pt": "PROTOCOLO · {protocol}",
        "fr": "PROTOCOLE · {protocol}",
        "zh": "协议 · {protocol}",
    },
    "window.no_tasks": {
        "en": "No background tasks yet.",
        "es": "Todavía no hay tareas en segundo plano.",
        "pt": "Ainda não há tarefas em segundo plano.",
        "fr": "Aucune tâche en arrière-plan.",
        "zh": "还没有后台任务。",
    },
    "window.no_routines": {
        "en": "No routines yet. Ask for one by voice — "
              "\"every weekday at 08:30, tell me the weather\".",
        "es": "Todavía no hay rutinas. Pide una por voz — "
              "\"cada día laborable a las 08:30, dime el tiempo\".",
        "pt": "Ainda não há rotinas. Peça uma por voz — "
              "\"todo dia útil às 08:30, me diga o tempo\".",
        "fr": "Aucune routine. Demandez-en une à la voix — "
              "\"chaque jour à 08:30, dis-moi la météo\".",
        "zh": "还没有例行任务。用语音提出一个——"
              "「每个工作日 08:30 告诉我天气」。",
    },
    "window.file_hint_folder": {
        "en": "{icon}  {name}  ·  {count} files  ·  Tell {assistant} what to change in it",
        "es": "{icon}  {name}  ·  {count} archivos  ·  Dile a {assistant} qué cambiar",
        "pt": "{icon}  {name}  ·  {count} ficheiros  ·  Diga a {assistant} o que mudar",
        "fr": "{icon}  {name}  ·  {count} fichiers  ·  Dites à {assistant} quoi changer",
        "zh": "{icon}  {name}  ·  {count} 个文件  ·  告诉 {assistant} 要改什么",
    },
    "window.file_hint_file": {
        "en": "{icon}  {name}  ·  {size}  ·  Tell {assistant} what to do with it",
        "es": "{icon}  {name}  ·  {size}  ·  Dile a {assistant} qué hacer con él",
        "pt": "{icon}  {name}  ·  {size}  ·  Diga a {assistant} o que fazer com ele",
        "fr": "{icon}  {name}  ·  {size}  ·  Dites à {assistant} qu'en faire",
        "zh": "{icon}  {name}  ·  {size}  ·  告诉 {assistant} 要怎么处理",
    },

    # -- the two drawers ----------------------------------------------------
    "controls.fullscreen": {
        "en": "⛶  FULLSCREEN",
        "es": "⛶  PANTALLA COMPLETA",
        "pt": "⛶  ECRÃ INTEIRO",
        "fr": "⛶  PLEIN ÉCRAN",
        "zh": "⛶  全屏",
    },
    "controls.remote": {
        "en": "◉  REMOTE CONTROL",
        "es": "◉  CONTROL REMOTO",
        "pt": "◉  CONTROLO REMOTO",
        "fr": "◉  TÉLÉCOMMANDE",
        "zh": "◉  远程控制",
    },
    "controls.shortcut": {
        "en": "⊞  CREATE DESKTOP SHORTCUT",
        "es": "⊞  CREAR ACCESO DIRECTO EN EL ESCRITORIO",
        "pt": "⊞  CRIAR ATALHO NA ÁREA DE TRABALHO",
        "fr": "⊞  CRÉER UN RACCOURCI SUR LE BUREAU",
        "zh": "⊞  创建桌面快捷方式",
    },
    "controls.autostart_on": {
        "en": "◉  AUTO-START: ON",
        "es": "◉  INICIO AUTOMÁTICO: SÍ",
        "pt": "◉  INÍCIO AUTOMÁTICO: SIM",
        "fr": "◉  DÉMARRAGE AUTO : OUI",
        "zh": "◉  开机自启：开",
    },
    "controls.autostart_off": {
        "en": "◉  AUTO-START: OFF",
        "es": "◉  INICIO AUTOMÁTICO: NO",
        "pt": "◉  INÍCIO AUTOMÁTICO: NÃO",
        "fr": "◉  DÉMARRAGE AUTO : NON",
        "zh": "◉  开机自启：关",
    },
    "controls.customise": {
        "en": "⚙  CUSTOMISE ASSISTANT",
        "es": "⚙  PERSONALIZAR EL ASISTENTE",
        "pt": "⚙  PERSONALIZAR O ASSISTENTE",
        "fr": "⚙  PERSONNALISER L'ASSISTANT",
        "zh": "⚙  自定义助手",
    },
    "controls.audio_devices": {
        "en": "🎧  AUDIO DEVICES",
        "es": "🎧  DISPOSITIVOS DE AUDIO",
        "pt": "🎧  DISPOSITIVOS DE ÁUDIO",
        "fr": "🎧  PÉRIPHÉRIQUES AUDIO",
        "zh": "🎧  音频设备",
    },
    "controls.memory": {
        "en": "🧠  MEMORY",
        "es": "🧠  MEMORIA",
        "pt": "🧠  MEMÓRIA",
        "fr": "🧠  MÉMOIRE",
        "zh": "🧠  记忆",
    },
    "controls.plugins": {
        "en": "🧩  PLUGINS",
        "es": "🧩  COMPLEMENTOS",
        "pt": "🧩  EXTENSÕES",
        "fr": "🧩  EXTENSIONS",
        "zh": "🧩  插件",
    },
    "controls.plugin_settings": {
        "en": "⚙  PLUGIN SETTINGS",
        "es": "⚙  AJUSTES DE COMPLEMENTOS",
        "pt": "⚙  CONFIGURAÇÃO DE EXTENSÕES",
        "fr": "⚙  RÉGLAGES DES EXTENSIONS",
        "zh": "⚙  插件设置",
    },
    "controls.engine_providers": {
        "en": "🔌  ENGINE && PROVIDERS",
        "es": "🔌  MOTOR Y PROVEEDORES",
        "pt": "🔌  MOTOR E PROVEDORES",
        "fr": "🔌  MOTEUR ET FOURNISSEURS",
        "zh": "🔌  引擎与服务商",
    },
    "controls.wake_word": {
        "en": "🎙  WAKE WORD",
        "es": "🎙  FRASE DE ACTIVACIÓN",
        "pt": "🎙  FRASE DE ATIVAÇÃO",
        "fr": "🎙  PHRASE D'ACTIVATION",
        "zh": "🎙  唤醒短语",
    },
    "controls.wake_download": {
        "en": "⬇  WAKE WORD: DOWNLOAD",
        "es": "⬇  FRASE DE ACTIVACIÓN: DESCARGAR",
        "pt": "⬇  FRASE DE ATIVAÇÃO: BAIXAR",
        "fr": "⬇  PHRASE D'ACTIVATION : TÉLÉCHARGER",
        "zh": "⬇  唤醒短语：下载",
    },
    "controls.wake_on": {
        "en": "🎙  WAKE WORD: ON",
        "es": "🎙  FRASE DE ACTIVACIÓN: SÍ",
        "pt": "🎙  FRASE DE ATIVAÇÃO: SIM",
        "fr": "🎙  PHRASE D'ACTIVATION : OUI",
        "zh": "🎙  唤醒短语：开",
    },
    "controls.wake_off": {
        "en": "🎙  WAKE WORD: OFF",
        "es": "🎙  FRASE DE ACTIVACIÓN: NO",
        "pt": "🎙  FRASE DE ATIVAÇÃO: NÃO",
        "fr": "🎙  PHRASE D'ACTIVATION : NON",
        "zh": "🎙  唤醒短语：关",
    },
    "controls.sleep_now": {
        "en": "😴  SLEEP NOW",
        "es": "😴  DORMIR AHORA",
        "pt": "😴  DORMIR AGORA",
        "fr": "😴  DORMIR MAINTENANT",
        "zh": "😴  现在休眠",
    },
    "controls.wake_now": {
        "en": "👂  WAKE NOW",
        "es": "👂  DESPERTAR AHORA",
        "pt": "👂  ACORDAR AGORA",
        "fr": "👂  RÉVEILLER MAINTENANT",
        "zh": "👂  现在唤醒",
    },
    "controls.downloading": {
        "en": "⬇  DOWNLOADING… (one-time)",
        "es": "⬇  DESCARGANDO… (una vez)",
        "pt": "⬇  BAIXANDO… (uma vez)",
        "fr": "⬇  TÉLÉCHARGEMENT… (une seule fois)",
        "zh": "⬇  下载中…（仅一次）",
    },
    "controls.wake_phrase_tip": {
        "en": "The words you want the assistant to answer to. They only take "
              "effect if a wake model trained for them is installed — see the "
              "line under this box.",
        "es": "Las palabras a las que quieres que responda el asistente. Solo "
              "surten efecto si hay instalado un modelo de activación entrenado "
              "con ellas; mira la línea de debajo de este campo.",
        "pt": "As palavras a que quer que o assistente responda. Só têm efeito "
              "se houver instalado um modelo de ativação treinado com elas — "
              "veja a linha por baixo deste campo.",
        "fr": "Les mots auxquels vous voulez que l'assistant réponde. Ils ne "
              "prennent effet que si un modèle d'activation entraîné pour eux "
              "est installé — regardez la ligne sous ce champ.",
        "zh": "你希望助手应答的词语。只有在安装了针对它们训练的唤醒模型时才有效——"
              "请看这个输入框下面的一行说明。",
    },
    "controls.wake_mode_tip": {
        "en": "Any voice wakes it up — no phrase to memorise and no model to "
              "train. A phrase needs a wake model trained for it.",
        "es": "Cualquier voz lo despierta: no hay frase que memorizar ni modelo "
              "que entrenar. Una frase necesita un modelo de activación "
              "entrenado para ella.",
        "pt": "Qualquer voz o acorda: não há frase para memorizar nem modelo "
              "para treinar. Uma frase precisa de um modelo de ativação "
              "treinado para ela.",
        "fr": "N'importe quelle voix le réveille — pas de phrase à mémoriser ni "
              "de modèle à entraîner. Une phrase exige un modèle d'activation "
              "entraîné pour elle.",
        "zh": "任何声音都能唤醒它——不用记短语，也不用训练模型。使用短语则需要一个"
              "针对它训练的唤醒模型。",
    },
    "controls.context_tip": {
        "en": "The tools the assistant is given. Narrowing it makes the prompt "
              "smaller and the model less likely to reach for the wrong one.",
        "es": "Las herramientas que se le dan al asistente. Reducirlas hace "
              "el prompt más pequeño y que el modelo coja menos la que no toca.",
        "pt": "As ferramentas que são dadas ao assistente. Reduzi-las torna o "
              "prompt menor e evita que o modelo escolha a errada.",
        "fr": "Les outils donnés à l'assistant. Les réduire rend le prompt plus "
              "petit et évite que le modèle saisisse le mauvais.",
        "zh": "给助手的工具。缩小这个范围会让提示更短，模型也更不容易拿错工具。",
    },
    "controls.context_none": {
        "en": "No tools yet",
        "es": "Todavía no hay herramientas",
        "pt": "Ainda não há ferramentas",
        "fr": "Pas encore d'outils",
        "zh": "还没有工具",
    },
    "controls.ptt_on": {
        "en": "🎚  PUSH-TO-TALK: {chord}",
        "es": "🎚  PULSA PARA HABLAR: {chord}",
        "pt": "🎚  PRESSIONE PARA FALAR: {chord}",
        "fr": "🎚  APPUYER POUR PARLER : {chord}",
        "zh": "🎚  按住说话：{chord}",
    },
    "controls.ptt_off": {
        "en": "🎚  PUSH-TO-TALK: OFF",
        "es": "🎚  PULSA PARA HABLAR: NO",
        "pt": "🎚  PRESSIONE PARA FALAR: NÃO",
        "fr": "🎚  APPUYER POUR PARLER : NON",
        "zh": "🎚  按住说话：关",
    },
    "controls.ptt_tip_on": {
        "en": "Microphone stays closed until you hold the key — nothing is sent "
              "while you are not holding it.",
        "es": "El micrófono se mantiene cerrado mientras no mantengas la tecla: "
              "no se envía nada mientras no la pulses.",
        "pt": "O microfone fica fechado enquanto não mantiver a tecla: nada é "
              "enviado enquanto não a premir.",
        "fr": "Le micro reste fermé tant que vous ne maintenez pas la touche : "
              "rien n'est envoyé le temps que vous ne la maintenez pas.",
        "zh": "不按住按键时麦克风保持关闭——不按住就不会发送任何内容。",
    },
    "controls.ptt_tip_off": {
        "en": "Hold a key to talk instead of streaming the mic continuously.",
        "es": "Mantén una tecla para hablar en vez de tener el micrófono "
              "abierto continuamente.",
        "pt": "Mantenha uma tecla para falar em vez de manter o microfone "
              "aberto continuamente.",
        "fr": "Maintenez une touche pour parler au lieu de laisser le micro "
              "ouvert en continu.",
        "zh": "按住一个键即可说话，而不必一直开着麦克风。",
    },
    "controls.hud_face": {
        "en": "🧑  HUD: ANIMATED FACE",
        "es": "🧑  HUD: CARA ANIMADA",
        "pt": "🧑  HUD: ROSTO ANIMADO",
        "fr": "🧑  HUD : VISAGE ANIMÉ",
        "zh": "🧑  HUD：动画头像",
    },
    "controls.hud_core": {
        "en": "◉  HUD: REACTOR CORE",
        "es": "◉  HUD: NÚCLEO DEL REACTOR",
        "pt": "◉  HUD: NÚCLEO DO REATOR",
        "fr": "◉  HUD : CŒUR DU RÉACTEUR",
        "zh": "◉  HUD：反应堆核心",
    },
    "controls.hud_tip_face": {
        "en": "An animated head that speaks your words and shows what the "
              "assistant is doing. Tap to switch to the reactor core.",
        "es": "Una cabeza animada que dice tus palabras y muestra lo que hace "
              "el asistente. Toca para cambiar al núcleo del reactor.",
        "pt": "Uma cabeça animada que diz as suas palavras e mostra o que o "
              "assistente está a fazer. Toque para mudar para o núcleo do reator.",
        "fr": "Une tête animée qui dit vos mots et montre ce que fait "
              "l'assistant. Touchez pour passer au cœur du réacteur.",
        "zh": "一个会说出你的话、并显示助手正在做什么的动画头像。点按可切换到反应堆核心。",
    },
    "controls.hud_tip_core": {
        "en": "A 3-D reactor core that turns with the state and moves with your "
              "voice. Tap to switch to the animated head.",
        "es": "Un núcleo de reactor en 3D que gira con el estado y se mueve con "
              "tu voz. Toca para cambiar a la cabeza animada.",
        "pt": "Um núcleo de reator em 3D que gira com o estado e mexe-se com a "
              "sua voz. Toque para mudar para o rosto animado.",
        "fr": "Un cœur de réacteur en 3D qui tourne selon l'état et bouge avec "
              "votre voix. Touchez pour passer au visage animé.",
        "zh": "一个随状态转动、随你的声音起伏的 3D 反应堆核心。点按可切换到动画头像。",
    },
    "controls.brief_on": {
        "en": "☀  MORNING BRIEF: ON",
        "es": "☀  RESUMEN MATUTINO: SÍ",
        "pt": "☀  RESUMO MATINAL: SIM",
        "fr": "☀  BRIEFING MATINAL : OUI",
        "zh": "☀  晨报：开",
    },
    "controls.brief_off": {
        "en": "☀  MORNING BRIEF: OFF",
        "es": "☀  RESUMEN MATUTINO: NO",
        "pt": "☀  RESUMO MATINAL: NÃO",
        "fr": "☀  BRIEFING MATINAL : NON",
        "zh": "☀  晨报：关",
    },

    # -- the wake word ------------------------------------------------------
    "wake.mode_phrase": {
        "en": "Wake on a phrase",
        "es": "Despertar con una frase",
        "pt": "Acordar com uma frase",
        "fr": "Réveil sur une phrase",
        "zh": "用短语唤醒",
    },
    "wake.mode_voice": {
        "en": "Wake on any voice",
        "es": "Despertar con cualquier voz",
        "pt": "Acordar com qualquer voz",
        "fr": "Réveil à la première voix",
        "zh": "用任意语音唤醒",
    },
    "wake.mode_changed": {
        "en": "Wake mode is now '{mode}' — {detail}.",
        "es": "El modo de activación ahora es '{mode}' — {detail}.",
        "pt": "O modo de ativação agora é '{mode}' — {detail}.",
        "fr": "Le mode d'activation est maintenant '{mode}' — {detail}.",
        "zh": "唤醒方式现在为「{mode}」——{detail}。",
    },
    "wake.detail_voice": {
        "en": "any voice wakes it up",
        "es": "cualquier voz lo despierta",
        "pt": "qualquer voz o acorda",
        "fr": "n'importe quelle voix le réveille",
        "zh": "任何声音都能唤醒它",
    },
    "wake.detail_phrase": {
        "en": "it listens for a phrase",
        "es": "escucha una frase",
        "pt": "ouve uma frase",
        "fr": "il écoute une phrase",
        "zh": "它会等待一个短语",
    },
    "wake.phrase_changed": {
        "en": "Wake phrase is now '{phrase}'.",
        "es": "La frase de activación ahora es '{phrase}'.",
        "pt": "A frase de ativação agora é '{phrase}'.",
        "fr": "La phrase d'activation est maintenant '{phrase}'.",
        "zh": "唤醒短语现在是「{phrase}」。",
    },
    "wake.effective_voice": {
        "en": "Microphone: wakes on ANY VOICE — say anything, no phrase needed.",
        "es": "Micrófono: se despierta con CUALQUIER VOZ: di lo que sea, no "
              "hace falta ninguna frase.",
        "pt": "Microfone: acorda com QUALQUER VOZ — diga o que quiser, não é "
              "precisa de nenhuma frase.",
        "fr": "Micro : il se réveille à N'IMPORTE QUELLE VOIX — dites ce que vous "
              "voulez, aucune phrase n'est nécessaire.",
        "zh": "麦克风：任何声音都能唤醒它——随便说什么都行，不需要短语。",
    },
    "wake.effective_phrase": {
        "en": "Microphone: listening for '{phrase}'.",
        "es": "Micrófono: escucha «{phrase}».",
        "pt": "Microfone: à escuta de «{phrase}».",
        "fr": "Micro : il écoute « {phrase} ».",
        "zh": "麦克风：正在等待「{phrase}」。",
    },
    "wake.effective_model": {
        "en": "Microphone: the installed wake model listens for '{effective}', "
              "NOT '{wanted}'. Either say '{effective}', or switch to "
              "'{mode}' above.",
        "es": "Micrófono: el modelo de activación instalado escucha «{effective}», "
              "NO «{wanted}». Di «{effective}» o cambia a «{mode}» arriba.",
        "pt": "Microfone: o modelo de ativação instalado ouve «{effective}», "
              "NÃO «{wanted}». Diga «{effective}» ou mude para «{mode}» acima.",
        "fr": "Micro : le modèle d'activation installé écoute « {effective} », et "
              "NON « {wanted} ». Dites « {effective} » ou passez à "
              "« {mode} » ci-dessus.",
        "zh": "麦克风：已安装的唤醒模型等待的是「{effective}」，而不是「{wanted}」。"
              "请说「{effective}」，或在上方切换到「{mode}」。",
    },
    "wake.effective_none": {
        "en": "Microphone: no wake model is installed, so '{wanted}' will not "
              "wake it up. Use '{mode}' or install a model trained for your "
              "phrase.",
        "es": "Micrófono: no hay ningún modelo de activación instalado, así que "
              "«{wanted}» no lo despertará. Usa «{mode}» o instala un modelo "
              "entrenado con tu frase.",
        "pt": "Microfone: não há nenhum modelo de ativação instalado, por isso "
              "«{wanted}» não o acordará. Use «{mode}» ou instale um modelo "
              "treinado com a sua frase.",
        "fr": "Micro : aucun modèle d'activation n'est installé, donc « {wanted} » "
              "ne le réveillera pas. Utilisez « {mode} » ou installez un modèle "
              "entraîné pour votre phrase.",
        "zh": "麦克风：没有安装唤醒模型，因此「{wanted}」无法唤醒它。请使用「{mode}」，"
              "或安装一个针对你的短语训练的模型。",
    },

    # -- the two selectors --------------------------------------------------
    "selector.language_tip": {
        "en": "The language of the interface and of the assistant's speech.",
        "es": "El idioma de la interfaz y de la voz del asistente.",
        "pt": "O idioma da interface e da voz do assistente.",
        "fr": "La langue de l'interface et de la voix de l'assistant.",
        "zh": "界面和助手语音使用的语言。",
    },
    "selector.region_tip": {
        "en": "Your city and country — used for the local weather and for "
              "national and regional news. You can type a city that is not "
              "on the list.",
        "es": "Tu ciudad y tu país: se usan para el clima local y las "
              "noticias nacionales y regionales. Puedes escribir una ciudad "
              "que no esté en la lista.",
        "pt": "A sua cidade e o seu país — usados para o tempo local e para as "
              "notícias nacionais e regionais. Pode escrever uma cidade que não "
              "esteja na lista.",
        "fr": "Votre ville et votre pays — utilisés pour la météo locale et "
              "pour les actualités nationales et régionales. Vous pouvez saisir "
              "une ville qui n'est pas dans la liste.",
        "zh": "你所在的城市和国家——用于本地天气以及全国和地区新闻。你也可以输入"
              "列表中没有的城市。",
    },
    "selector.explain": {
        "en": "The region sets the weather and the news. The language changes "
              "the whole interface and the assistant's speech.",
        "es": "La región decide el clima y las noticias. El idioma cambia toda "
              "la interfaz y la voz del asistente.",
        "pt": "A região define o tempo e as notícias. O idioma muda toda a "
              "interface e a voz do assistente.",
        "fr": "La région règle la météo et les nouvelles. La langue change "
              "toute l'interface et la voix de l'assistant.",
        "zh": "地区决定天气和新闻。语言会改变整个界面以及助手说话的语言。",
    },
    "selector.language_changed": {
        "en": "Language is now {language}.",
        "es": "El idioma ahora es {language}.",
        "pt": "O idioma agora é {language}.",
        "fr": "La langue est maintenant {language}.",
        "zh": "语言现在为 {language}。",
    },
    "selector.region_changed": {
        "en": "Region is now {region}.",
        "es": "La región ahora es {region}.",
        "pt": "A região agora é {region}.",
        "fr": "La région est maintenant {region}.",
        "zh": "地区现在为 {region}。",
    },

    # -- the HUD ------------------------------------------------------------
    "hud.voice_interface": {
        "en": "VOICE INTERFACE",
        "es": "INTERFAZ DE VOZ",
        "pt": "INTERFACE DE VOZ",
        "fr": "INTERFACE VOCALE",
        "zh": "语音界面",
    },
    "hud.visual_input": {
        "en": "◈  VISUAL INPUT",
        "es": "◈  ENTRADA VISUAL",
        "pt": "◈  ENTRADA VISUAL",
        "fr": "◈  ENTRÉE VISUELLE",
        "zh": "◈  视觉输入",
    },
    "hud.visual_input_hint": {
        "en": "The last picture the camera took. It closes by itself.",
        "es": "La última foto que tomó la cámara. Se cierra sola.",
        "pt": "A última foto que a câmara tirou. Fecha-se sozinha.",
        "fr": "La dernière photo prise par la caméra. Elle se ferme seule.",
        "zh": "摄像头拍摄的最后一张照片。它会自动关闭。",
    },
    "hud.state.muted": {
        "en": "MUTED",
        "es": "SILENCIADO",
        "pt": "MUDO",
        "fr": "COUPÉ",
        "zh": "已静音",
    },
    "hud.state.listening": {
        "en": "LISTENING",
        "es": "ESCUCHANDO",
        "pt": "OUVINDO",
        "fr": "À L'ÉCOUTE",
        "zh": "正在聆听",
    },
    "hud.state.speaking": {
        "en": "SPEAKING",
        "es": "HABLANDO",
        "pt": "FALANDO",
        "fr": "PARLANT",
        "zh": "正在说话",
    },
    "hud.state.thinking": {
        "en": "THINKING",
        "es": "PENSANDO",
        "pt": "PENSANDO",
        "fr": "RÉFLEXION",
        "zh": "思考中",
    },
    "hud.state.processing": {
        "en": "PROCESSING",
        "es": "PROCESANDO",
        "pt": "PROCESSANDO",
        "fr": "TRAITEMENT",
        "zh": "处理中",
    },
    "hud.state.idle": {
        "en": "IDLE",
        "es": "INACTIVO",
        "pt": "OCIOSO",
        "fr": "INACTIF",
        "zh": "空闲",
    },
    "hud.state.initialising": {
        "en": "INITIALISING",
        "es": "INICIANDO",
        "pt": "INICIANDO",
        "fr": "INITIALISATION",
        "zh": "正在初始化",
    },

    # -- the quiz board -----------------------------------------------------
    "quiz.next": {
        "en": "NEXT  →",
        "es": "SIGUIENTE  →",
        "pt": "PRÓXIMO  →",
        "fr": "SUIVANT  →",
        "zh": "下一题  →",
    },
    "quiz.finish": {
        "en": "FINISH  →",
        "es": "TERMINAR  →",
        "pt": "TERMINAR  →",
        "fr": "TERMINER  →",
        "zh": "完成  →",
    },
    "quiz.answer": {
        "en": "ANSWER",
        "es": "RESPONDER",
        "pt": "RESPONDER",
        "fr": "RÉPONDRE",
        "zh": "回答",
    },
    "quiz.your_answer": {
        "en": "your answer",
        "es": "tu respuesta",
        "pt": "sua resposta",
        "fr": "votre réponse",
        "zh": "你的答案",
    },
    "quiz.correct": {
        "en": "✓  correct",
        "es": "✓  correcto",
        "pt": "✓  correto",
        "fr": "✓  juste",
        "zh": "✓  正确",
    },
    "quiz.noted": {
        "en": "…  noted — I'll go over this one with you",
        "es": "…  anotado — lo repasaremos juntos",
        "pt": "…  anotado — vou rever esta consigo",
        "fr": "…  noté — on la reverra ensemble",
        "zh": "…  已记录——这道我们一起看",
    },

    # -- the overlays -------------------------------------------------------
    "overlay.initialise_required": {
        "en": "◈  INITIALISATION REQUIRED",
        "es": "◈  SE REQUIERE INICIALIZACIÓN",
        "pt": "◈  É NECESSÁRIA INICIALIZAÇÃO",
        "fr": "◈  INITIALISATION REQUISE",
        "zh": "◈  需要初始化",
    },
    "overlay.configure_before_boot": {
        "en": "Configure T.E.E.M.O. before first boot.",
        "es": "Configura T.E.E.M.O. antes del primer arranque.",
        "pt": "Configure o T.E.E.M.O. antes do primeiro arranque.",
        "fr": "Configurez T.E.E.M.O. avant le premier démarrage.",
        "zh": "首次启动前请先配置 T.E.E.M.O.。",
    },
    "overlay.gemini_api_key": {
        "en": "GEMINI API KEY",
        "es": "CLAVE API DE GEMINI",
        "pt": "CHAVE DE API DO GEMINI",
        "fr": "CLÉ API GEMINI",
        "zh": "GEMINI API 密钥",
    },
    "overlay.operating_system": {
        "en": "OPERATING SYSTEM",
        "es": "SISTEMA OPERATIVO",
        "pt": "SISTEMA OPERATIVO",
        "fr": "SYSTÈME D'EXPLOITATION",
        "zh": "操作系统",
    },
    "overlay.initialise": {
        "en": "▸  INITIALISE SYSTEMS",
        "es": "▸  INICIALIZAR SISTEMAS",
        "pt": "▸  INICIALIZAR SISTEMAS",
        "fr": "▸  INITIALISER LES SYSTÈMES",
        "zh": "▸  初始化系统",
    },
    "overlay.no_key_local": {
        "en": "NO KEY — USE A LOCAL MODEL",
        "es": "SIN CLAVE — USA UN MODELO LOCAL",
        "pt": "SEM CHAVE — USE UM MODELO LOCAL",
        "fr": "PAS DE CLÉ — UTILISEZ UN MODÈLE LOCAL",
        "zh": "无密钥——使用本地模型",
    },
    "overlay.assistant_name": {
        "en": "ASSISTANT NAME",
        "es": "NOMBRE DEL ASISTENTE",
        "pt": "NOME DO ASSISTENTE",
        "fr": "NOM DE L'ASSISTANT",
        "zh": "助手名称",
    },
    "overlay.your_name": {
        "en": "YOUR NAME  (leave blank for default sir / efendim)",
        "es": "TU NOMBRE  (déjalo vacío para el señor / efendim por defecto)",
        "pt": "O SEU NOME  (deixe em branco para o senhor / efendim por omissão)",
        "fr": "VOTRE NOM  (laissez vide pour monsieur / efendim par défaut)",
        "zh": "你的名字  （留空则默认使用先生／efendim）",
    },
    "overlay.user_name_hint": {
        "en": "e.g.  Tony   (leave blank for auto)",
        "es": "p. ej.  Tony   (déjalo vacío para automático)",
        "pt": "ex.  Tony   (deixe em branco para automático)",
        "fr": "ex.  Tony   (laissez vide pour automatique)",
        "zh": "例如  Tony   （留空则自动）",
    },
    "overlay.assistant_voice": {
        "en": "ASSISTANT VOICE",
        "es": "VOZ DEL ASISTENTE",
        "pt": "VOZ DO ASSISTENTE",
        "fr": "VOIX DE L'ASSISTANT",
        "zh": "助手的声音",
    },
    "overlay.ui_colour": {
        "en": "UI COLOUR  —  drag the handle",
        "es": "COLOR DE LA INTERFAZ  —  arrastra el mango",
        "pt": "COR DA INTERFACE  —  arraste a pega",
        "fr": "COULEUR DE L'INTERFACE  —  glissez la poignée",
        "zh": "界面颜色  —  拖动手柄",
    },
    "overlay.default": {
        "en": "DEFAULT",
        "es": "PREDETERMINADO",
        "pt": "PADRÃO",
        "fr": "PAR DÉFAUT",
        "zh": "默认",
    },
    "overlay.custom_hex": {
        "en": "(custom hex colour)",
        "es": "(color hexadecimal personalizado)",
        "pt": "(cor hexadecimal personalizada)",
        "fr": "(couleur hexadécimale personnalisée)",
        "zh": "（自定义十六进制颜色）",
    },
    "overlay.apply_changes": {
        "en": "▸  APPLY CHANGES",
        "es": "▸  APLICAR CAMBIOS",
        "pt": "▸  APLICAR ALTERAÇÕES",
        "fr": "▸  APPLIQUER LES MODIFICATIONS",
        "zh": "▸  应用更改",
    },
    "overlay.plugin_manager": {
        "en": "🧩  PLUGIN MANAGER",
        "es": "🧩  GESTOR DE COMPLEMENTOS",
        "pt": "🧩  GESTOR DE EXTENSÕES",
        "fr": "🧩  GESTIONNAIRE D'EXTENSIONS",
        "zh": "🧩  插件管理",
    },
    "overlay.no_plugins": {
        "en": "No plugins found in /plugins.",
        "es": "No se encontraron complementos en /plugins.",
        "pt": "Não foram encontradas extensões em /plugins.",
        "fr": "Aucune extension trouvée dans /plugins.",
        "zh": "在 /plugins 中没有找到插件。",
    },
    "overlay.broken": {
        "en": "BROKEN",
        "es": "ROTO",
        "pt": "PARTIDO",
        "fr": "CASSÉ",
        "zh": "损坏",
    },
    "overlay.on": {
        "en": "ON",
        "es": "SÍ",
        "pt": "SIM",
        "fr": "OUI",
        "zh": "开",
    },
    "overlay.off": {
        "en": "OFF",
        "es": "NO",
        "pt": "NÃO",
        "fr": "NON",
        "zh": "关",
    },
    "overlay.confirm_title": {
        "en": "⚠  CONFIRM",
        "es": "⚠  CONFIRMAR",
        "pt": "⚠  CONFIRMAR",
        "fr": "⚠  CONFIRMER",
        "zh": "⚠  确认",
    },
    "overlay.confirm_go": {
        "en": "▸  CONFIRM",
        "es": "▸  CONFIRMAR",
        "pt": "▸  CONFIRMAR",
        "fr": "▸  CONFIRMER",
        "zh": "▸  确认",
    },
    "overlay.audio_devices": {
        "en": "🎧  AUDIO DEVICES",
        "es": "🎧  DISPOSITIVOS DE AUDIO",
        "pt": "🎧  DISPOSITIVOS DE ÁUDIO",
        "fr": "🎧  PÉRIPHÉRIQUES AUDIO",
        "zh": "🎧  音频设备",
    },
    "overlay.microphone_caption": {
        "en": "MICROPHONE — what the assistant hears you with",
        "es": "MICRÓFONO — con lo que el asistente te oye",
        "pt": "MICROFONE — com o que o assistente ouve você",
        "fr": "MICRO — ce avec quoi l'assistant vous entend",
        "zh": "麦克风——助手用它听你说话",
    },
    "overlay.speakers_caption": {
        "en": "SPEAKERS — what the assistant talks through",
        "es": "ALTAVOCES — por donde habla el asistente",
        "pt": "COLUNAS — por onde fala o assistente",
        "fr": "ENCEINTES — par où parle l'assistant",
        "zh": "扬声器——助手用它说话",
    },
    "overlay.test": {
        "en": "TEST",
        "es": "PROBAR",
        "pt": "TESTAR",
        "fr": "TESTER",
        "zh": "测试",
    },
    "overlay.not_connected": {
        "en": "(not connected)",
        "es": "(sin conectar)",
        "pt": "(não ligado)",
        "fr": "(non connecté)",
        "zh": "（未连接）",
    },
    "overlay.output_volume": {
        "en": "OUTPUT VOLUME",
        "es": "VOLUMEN DE SALIDA",
        "pt": "VOLUME DE SAÍDA",
        "fr": "VOLUME DE SORTIE",
        "zh": "输出音量",
    },
    "overlay.apply_reconnects": {
        "en": "Applying reconnects the session. Your conversation is kept.",
        "es": "Aplicar reconecta la sesión. Tu conversación se conserva.",
        "pt": "Aplicar reconecta a sessão. A sua conversa é mantida.",
        "fr": "L'application reconnecte la session. Votre conversation est conservée.",
        "zh": "应用后会重新连接。您的对话会保留。",
    },
    "overlay.apply": {
        "en": "▸  APPLY",
        "es": "▸  APLICAR",
        "pt": "▸  APLICAR",
        "fr": "▸  APPLIQUER",
        "zh": "▸  应用",
    },
    "overlay.speak_now": {
        "en": "Speak now…",
        "es": "Habla ahora…",
        "pt": "Fale agora…",
        "fr": "Parlez maintenant…",
        "zh": "请说话…",
    },
    "overlay.playing_tone": {
        "en": "Playing a tone…",
        "es": "Reproduciendo un tono…",
        "pt": "A tocar um tom…",
        "fr": "Lecture d'un signal…",
        "zh": "正在播放提示音…",
    },
    "overlay.memory_title": {
        "en": "🧠  WHAT THE ASSISTANT REMEMBERS",
        "es": "🧠  LO QUE RECUERDA EL ASISTENTE",
        "pt": "🧠  O QUE O ASSISTENTE LEMBRA",
        "fr": "🧠  CE QUE L'ASSISTANT RETIENT",
        "zh": "🧠  助手记得的内容",
    },
    "overlay.memory_caption": {
        "en": "{count} stored facts — newest first. Nothing here is sent anywhere; "
              "it lives in memory/long_term.json on this machine.",
        "es": "{count} datos guardados: los más recientes primero. Nada de esto "
              "se envía a ninguna parte; vive en memory/long_term.json en este "
              "equipo.",
        "pt": "{count} factos guardados — os mais recentes primeiro. Nada disto "
              "é enviado para lado nenhum; vive em memory/long_term.json nesta "
              "máquina.",
        "fr": "{count} faits enregistrés — les plus récents d'abord. Rien de tout "
              "cela n'est envoyé ailleurs ; ils vivent dans memory/long_term.json "
              "sur cette machine.",
        "zh": "已保存 {count} 条记忆——最新的在前。这里的内容不会发送到任何地方，"
              "它保存在本机的 memory/long_term.json 中。",
    },
    "overlay.memory_empty": {
        "en": "Nothing stored yet.",
        "es": "Todavía no hay nada guardado.",
        "pt": "Nada guardado ainda.",
        "fr": "Rien d'enregistré pour l'instant.",
        "zh": "尚未保存任何内容。",
    },
    "overlay.forget_this": {
        "en": "Forget this",
        "es": "Olvidar esto",
        "pt": "Esquecer isto",
        "fr": "Oublier ceci",
        "zh": "忘掉这条",
    },
    "overlay.clipboard_detected": {
        "en": "◈  CLIPBOARD DETECTED",
        "es": "◈  PORTAPAPELES DETECTADO",
        "pt": "◈  CLIPBOARD DETECTADO",
        "fr": "◈  PRESSE-PAPIERS DÉTECTÉ",
        "zh": "◈  检测到剪贴板内容",
    },
    "overlay.translate": {
        "en": "TRANSLATE",
        "es": "TRADUCIR",
        "pt": "TRADUZIR",
        "fr": "TRADUIRE",
        "zh": "翻译",
    },
    "overlay.summarise": {
        "en": "SUMMARISE",
        "es": "RESUMIR",
        "pt": "RESUMIR",
        "fr": "RÉSUMER",
        "zh": "总结",
    },
    "overlay.explain": {
        "en": "EXPLAIN",
        "es": "EXPLICAR",
        "pt": "EXPLICAR",
        "fr": "EXPLIQUER",
        "zh": "解释",
    },
    "overlay.fix": {
        "en": "FIX",
        "es": "CORREGIR",
        "pt": "CORRIGIR",
        "fr": "CORRIGER",
        "zh": "修正",
    },
    "overlay.plugin_settings": {
        "en": "⚙  PLUGIN SETTINGS",
        "es": "⚙  AJUSTES DE COMPLEMENTOS",
        "pt": "⚙  CONFIGURAÇÃO DE EXTENSÕES",
        "fr": "⚙  RÉGLAGES DES EXTENSIONS",
        "zh": "⚙  插件设置",
    },
    "overlay.no_configurable_plugins": {
        "en": "No configurable plugins are installed.\nDrop a plugin that needs "
              "settings (like the 3D-printer suite) into the plugins folder and "
              "it will show up here.",
        "es": "No hay complementos configurables instalados.\nColoca en la "
              "carpeta de complementos uno que necesite ajustes (como el suite "
              "de impresión 3D) y aparecerá aquí.",
        "pt": "Não há extensões configuráveis instaladas.\nColoque na pasta de "
              "extensões uma que precise de definições (como o conjunto de "
              "impressora 3D) e ela aparecerá aqui.",
        "fr": "Aucune extension configurable n'est installée.\nDéposez dans le "
              "dossier des extensions une extension qui a besoin de réglages "
              "(comme la suite d'impression 3D) et elle apparaîtra ici.",
        "zh": "尚未安装可配置的插件。\n把需要设置的插件（例如 3D 打印套件）放进插件文件夹，"
              "它就会出现在这里。",
    },
    "overlay.saved_check": {
        "en": "Saved ✓",
        "es": "Guardado ✓",
        "pt": "Salvo ✓",
        "fr": "Enregistré ✓",
        "zh": "已保存 ✓",
    },
    "overlay.testing": {
        "en": "Testing…",
        "es": "Probando…",
        "pt": "A testar…",
        "fr": "Test en cours…",
        "zh": "正在测试…",
    },
    "overlay.new_key": {
        "en": "NEW KEY",
        "es": "NUEVA CLAVE",
        "pt": "NOVA CHAVE",
        "fr": "NOUVELLE CLÉ",
        "zh": "新密钥",
    },
    "overlay.dismiss": {
        "en": "DISMISS",
        "es": "DESCARTAR",
        "pt": "DISPENSAR",
        "fr": "IGNORER",
        "zh": "关闭",
    },
    "overlay.key_expires": {
        "en": "Key expires in  {minutes}:{seconds}",
        "es": "La clave caduca en  {minutes}:{seconds}",
        "pt": "A chave expira em  {minutes}:{seconds}",
        "fr": "La clé expire dans  {minutes}:{seconds}",
        "zh": "密钥将在 {minutes}:{seconds} 后过期",
    },
    "overlay.connected": {
        "en": "CONNECTED",
        "es": "CONECTADO",
        "pt": "LIGADO",
        "fr": "CONNECTÉ",
        "zh": "已连接",
    },
    "overlay.phone_connected": {
        "en": "Phone connected — assistant ready",
        "es": "Teléfono conectado: asistente listo",
        "pt": "Telefone ligado — assistente pronto",
        "fr": "Téléphone connecté — assistant prêt",
        "zh": "手机已连接——助手已就绪",
    },
    "overlay.remote_access": {
        "en": "◈  REMOTE ACCESS",
        "es": "◈  ACCESO REMOTO",
        "pt": "◈  ACESSO REMOTO",
        "fr": "◈  ACCÈS À DISTANCE",
        "zh": "◈  远程访问",
    },
    "overlay.scan_with_phone": {
        "en": "Scan with phone camera to connect instantly",
        "es": "Escanea con la cámara del teléfono para conectarte al instante",
        "pt": "Digitalize com a câmara do telemóvel para ligar de imediato",
        "fr": "Scannez avec l'appareil photo du téléphone pour vous connecter "
              "instantanément",
        "zh": "用手机相机扫码即可立即连接",
    },
    "overlay.firewall_blocked": {
        "en": "⚠  Windows is blocking incoming connections to this copy of "
              "Python, so your phone cannot reach this page. Restart the "
              "assistant and accept the Windows prompt, or remove the rules "
              "named {rules} under Windows Defender Firewall → Inbound Rules.",
        "es": "⚠  Windows está bloqueando las conexiones entrantes a esta copia "
              "de Python, así que tu teléfono no puede llegar a esta página. "
              "Reinicia el asistente y acepta el aviso de Windows, o quita las "
              "reglas llamadas {rules} en Firewall de Windows Defender → Reglas "
              "de entrada.",
        "pt": "⚠  O Windows está a bloquear ligações de entrada para esta cópia "
              "do Python, por isso o seu telemóvel não consegue chegar a esta "
              "página. Reinicie o assistente e aceite o aviso do Windows, ou "
              "remova as regras chamadas {rules} em Firewall do Windows "
              "Defender → Regras de Entrada.",
        "fr": "⚠  Windows bloque les connexions entrantes vers cette copie de "
              "Python : votre téléphone ne peut pas atteindre cette page. "
              "Redémarrez l'assistant et acceptez l'invite de Windows, ou "
              "supprimez les règles nommées {rules} dans Pare-feu Windows "
              "Defender → Règles de connexion entrante.",
        "zh": "⚠  Windows 正在阻止连入这份 Python 副本，因此您的手机无法访问此页面。"
              "请重启助手并接受 Windows 提示，或在「Windows Defender 防火墙 → "
              "入站规则」中删除名为 {rules} 的规则。",
    },
    "overlay.enter_manually": {
        "en": "Or enter manually:",
        "es": "O escríbelo a mano:",
        "pt": "Ou escreva manualmente:",
        "fr": "Ou saisissez à la main :",
        "zh": "或手动输入：",
    },

    # -- files --------------------------------------------------------------
    "file.choose_file": {
        "en": "📄  Choose a file…",
        "es": "📄  Elegir un archivo…",
        "pt": "📄  Escolher um ficheiro…",
        "fr": "📄  Choisir un fichier…",
        "zh": "📄  选择文件…",
    },
    "file.choose_folder": {
        "en": "📁  Choose a folder…  (for coding)",
        "es": "📁  Elegir una carpeta…  (para programar)",
        "pt": "📁  Escolher uma pasta…  (para programar)",
        "fr": "📁  Choisir un dossier…  (pour coder)",
        "zh": "📁  选择文件夹…（用于编程）",
    },
    "file.select_project_folder": {
        "en": "Select a project folder for T.E.E.M.O.",
        "es": "Selecciona una carpeta de proyecto para T.E.E.M.O.",
        "pt": "Selecione uma pasta de projeto para o T.E.E.M.O.",
        "fr": "Choisissez un dossier de projet pour T.E.E.M.O.",
        "zh": "为 T.E.E.M.O. 选择项目文件夹",
    },
    "file.select_file": {
        "en": "Select a file for T.E.E.M.O.",
        "es": "Selecciona un archivo para T.E.E.M.O.",
        "pt": "Selecione um ficheiro para o T.E.E.M.O.",
        "fr": "Choisissez un fichier pour T.E.E.M.O.",
        "zh": "为 T.E.E.M.O. 选择文件",
    },
    "file.filter_all": {
        "en": "All Files (*.*)",
        "es": "Todos los archivos (*.*)",
        "pt": "Todos os ficheiros (*.*)",
        "fr": "Tous les fichiers (*.*)",
        "zh": "所有文件 (*.*)",
    },
    "file.filter_images": {
        "en": "Image Files (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg)",
        "es": "Archivos de imagen (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg)",
        "pt": "Ficheiros de imagem (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg)",
        "fr": "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg)",
        "zh": "图片文件 (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg)",
    },
    "file.filter_documents": {
        "en": "Document Files (*.pdf *.docx *.txt *.md *.pptx)",
        "es": "Archivos de documento (*.pdf *.docx *.txt *.md *.pptx)",
        "pt": "Ficheiros de documento (*.pdf *.docx *.txt *.md *.pptx)",
        "fr": "Documents (*.pdf *.docx *.txt *.md *.pptx)",
        "zh": "文档文件 (*.pdf *.docx *.txt *.md *.pptx)",
    },
    "file.filter_data": {
        "en": "Data Files (*.csv *.xlsx *.json *.xml)",
        "es": "Archivos de datos (*.csv *.xlsx *.json *.xml)",
        "pt": "Ficheiros de dados (*.csv *.xlsx *.json *.xml)",
        "fr": "Données (*.csv *.xlsx *.json *.xml)",
        "zh": "数据文件 (*.csv *.xlsx *.json *.xml)",
    },
    "file.filter_code": {
        "en": "Source Files (*.py *.js *.ts *.html *.css *.java *.cpp *.go)",
        "es": "Archivos de código (*.py *.js *.ts *.html *.css *.java *.cpp *.go)",
        "pt": "Ficheiros de código (*.py *.js *.ts *.html *.css *.java *.cpp *.go)",
        "fr": "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go)",
        "zh": "源文件 (*.py *.js *.ts *.html *.css *.java *.cpp *.go)",
    },
    "file.filter_audio": {
        "en": "Audio Files (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)",
        "es": "Archivos de audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)",
        "pt": "Ficheiros de áudio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)",
        "fr": "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)",
        "zh": "音频文件 (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)",
    },
    "file.filter_video": {
        "en": "Video Files (*.mp4 *.avi *.mov *.mkv *.wmv *.webm)",
        "es": "Archivos de vídeo (*.mp4 *.avi *.mov *.mkv *.wmv *.webm)",
        "pt": "Ficheiros de vídeo (*.mp4 *.avi *.mov *.mkv *.wmv *.webm)",
        "fr": "Vidéos (*.mp4 *.avi *.mov *.mkv *.wmv *.webm)",
        "zh": "视频文件 (*.mp4 *.avi *.mov *.mkv *.wmv *.webm)",
    },
    "file.filter_archives": {
        "en": "Archive Files (*.zip *.rar *.tar *.gz *.7z)",
        "es": "Archivos comprimidos (*.zip *.rar *.tar *.gz *.7z)",
        "pt": "Ficheiros comprimidos (*.zip *.rar *.tar *.gz *.7z)",
        "fr": "Archives (*.zip *.rar *.tar *.gz *.7z)",
        "zh": "压缩文件 (*.zip *.rar *.tar *.gz *.7z)",
    },
    "file.drop_hint": {
        "en": "DROP A FILE OR FOLDER  ·  OR CLICK",
        "es": "SUELTA UN ARCHIVO O UNA CARPETA  ·  O HAZ CLIC",
        "pt": "SOLTE UM FICHEIRO OU UMA PASTA  ·  OU CLIQUE",
        "fr": "DÉPOSEZ UN FICHIER OU UN DOSSIER  ·  OU CLIQUEZ",
        "zh": "拖放文件或文件夹  ·  或点击",
    },
    "file.release_to_load": {
        "en": "RELEASE TO LOAD",
        "es": "SUELTA PARA CARGAR",
        "pt": "SOLTE PARA CARREGAR",
        "fr": "DÉPOSEZ POUR CHARGER",
        "zh": "松开以加载",
    },
    "file.kind_file": {
        "en": "FILE",
        "es": "ARCHIVO",
        "pt": "FICHEIRO",
        "fr": "FICHIER",
        "zh": "文件",
    },
    "file.kind_folder": {
        "en": "FOLDER",
        "es": "CARPETA",
        "pt": "PASTA",
        "fr": "DOSSIER",
        "zh": "文件夹",
    },
    "file.count": {
        "en": "{n} files",
        "es": "{n} archivos",
        "pt": "{n} ficheiros",
        "fr": "{n} fichiers",
        "zh": "{n} 个文件",
    },
    "file.count_more": {
        "en": "{n}+ files",
        "es": "{n}+ archivos",
        "pt": "{n}+ ficheiros",
        "fr": "{n}+ fichiers",
        "zh": "{n}+ 个文件",
    },

    # -- the activity log ----------------------------------------------------
    # The "SYS:", "ERR:", "FILE:", "QUIZ:" prefixes stay as they are: the log
    # is read by machines as well as by people. What follows them is prose, and
    # prose a person reads.
    "log.video_unavailable": {
        "en": "Video playback is not available in this Qt install.",
        "es": "La reproducción de vídeo no está disponible en esta instalación de Qt.",
        "pt": "A reprodução de vídeo não está disponível nesta instalação do Qt.",
        "fr": "La lecture vidéo n'est pas disponible dans cette installation de Qt.",
        "zh": "此 Qt 安装不支持视频播放。",
    },
    "log.image_format": {
        "en": "'{name}' could not be shown — this Qt build does not read that "
              "image format.",
        "es": "No se pudo mostrar «{name}»: esta compilación de Qt no lee ese "
              "formato de imagen.",
        "pt": "Não foi possível mostrar «{name}» — esta compilação do Qt não lê "
              "esse formato de imagem.",
        "fr": "« {name} » n'a pas pu être affiché — cette version de Qt ne lit "
              "pas ce format d'image.",
        "zh": "无法显示「{name}」——此 Qt 版本不支持该图片格式。",
    },
    "log.video_failed": {
        "en": "The video could not be played{error}.",
        "es": "No se pudo reproducir el vídeo{error}.",
        "pt": "Não foi possível reproduzir o vídeo{error}.",
        "fr": "La vidéo n'a pas pu être lue{error}.",
        "zh": "无法播放该视频{error}。",
    },
    "log.shortcut_created": {
        "en": "Desktop shortcut created.",
        "es": "Acceso directo del escritorio creado.",
        "pt": "Atalho na área de trabalho criado.",
        "fr": "Raccourci de bureau créé.",
        "zh": "已创建桌面快捷方式。",
    },
    "log.remote_unavailable": {
        "en": "Dashboard not running — remote unavailable.",
        "es": "El panel no está en marcha: control remoto no disponible.",
        "pt": "O painel não está a correr — remoto indisponível.",
        "fr": "Tableau de bord inactif — accès à distance indisponible.",
        "zh": "面板未运行——远程不可用。",
    },
    "log.remote_key_failed": {
        "en": "Could not generate remote key.",
        "es": "No se pudo generar la clave remota.",
        "pt": "Não foi possível gerar a chave remota.",
        "fr": "Impossible de générer la clé distante.",
        "zh": "无法生成远程密钥。",
    },
    "log.remote_key_generated": {
        "en": "Remote key generated — manual: {manual}",
        "es": "Clave remota generada — manual: {manual}",
        "pt": "Chave remota gerada — manual: {manual}",
        "fr": "Clé distante générée — manuelle : {manual}",
        "zh": "远程密钥已生成——手动地址：{manual}",
    },
    "log.autostart_enabled": {
        "en": "Auto-start enabled.",
        "es": "Inicio automático activado.",
        "pt": "Início automático ativado.",
        "fr": "Démarrage automatique activé.",
        "zh": "已启用开机自启。",
    },
    "log.autostart_disabled": {
        "en": "Auto-start disabled.",
        "es": "Inicio automático desactivado.",
        "pt": "Início automático desativado.",
        "fr": "Démarrage automatique désactivé.",
        "zh": "已关闭开机自启。",
    },
    "log.hud_face": {
        "en": "HUD switched to the animated face.",
        "es": "El HUD pasó a la cara animada.",
        "pt": "O HUD passou para o rosto animado.",
        "fr": "Le HUD est passé au visage animé.",
        "zh": "HUD 已切换为动画头像。",
    },
    "log.hud_core": {
        "en": "HUD switched to the reactor core.",
        "es": "El HUD pasó al núcleo del reactor.",
        "pt": "O HUD passou para o núcleo do reator.",
        "fr": "Le HUD est passé au cœur du réacteur.",
        "zh": "HUD 已切换为反应堆核心。",
    },
    "log.push_to_talk_failed": {
        "en": "Push-to-talk failed — {error}",
        "es": "El modo pulsa-para-hablar falló: {error}",
        "pt": "O modo pressione-para-falar falhou — {error}",
        "fr": "Le mode parler-maintenu a échoué — {error}",
        "zh": "按住说话失败——{error}",
    },
    "log.wake_word_ready": {
        "en": "Wake word ready.",
        "es": "La frase de activación está lista.",
        "pt": "A frase de ativação está pronta.",
        "fr": "La phrase d'activation est prête.",
        "zh": "唤醒短语已就绪。",
    },
    "log.wake_word_failed": {
        "en": "Wake word setup failed: {message}",
        "es": "No se pudo configurar la frase de activación: {message}",
        "pt": "Não foi possível configurar a frase de ativação: {message}",
        "fr": "Échec de la configuration de la phrase d'activation : {message}",
        "zh": "唤醒短语设置失败：{message}",
    },
    "log.identity_updated": {
        "en": "Identity updated — {name}",
        "es": "Identidad actualizada: {name}",
        "pt": "Identidade atualizada — {name}",
        "fr": "Identité mise à jour — {name}",
        "zh": "身份已更新——{name}",
    },
    "log.ui_colour_applied": {
        "en": "UI colour applied — {colour}",
        "es": "Color de la interfaz aplicado: {colour}",
        "pt": "Cor da interface aplicada — {colour}",
        "fr": "Couleur de l'interface appliquée — {colour}",
        "zh": "已应用界面颜色——{colour}",
    },
    "log.voice_set": {
        "en": "Voice set — {voice}",
        "es": "Voz establecida: {voice}",
        "pt": "Voz definida — {voice}",
        "fr": "Voix définie — {voice}",
        "zh": "声音已设置——{voice}",
    },
    "log.audio_devices_updated": {
        "en": "Audio devices updated.",
        "es": "Dispositivos de audio actualizados.",
        "pt": "Dispositivos de áudio atualizados.",
        "fr": "Périphériques audio mis à jour.",
        "zh": "音频设备已更新。",
    },
    "log.engine_settings_saved": {
        "en": "Engine settings saved — reconnecting.",
        "es": "Ajustes del motor guardados: reconectando.",
        "pt": "Definições do motor guardadas — a reconectar.",
        "fr": "Réglages du moteur enregistrés — reconnexion.",
        "zh": "引擎设置已保存——正在重新连接。",
    },
    "log.providers_missing": {
        "en": "The providers folder is not installed — the assistant runs on "
              "Gemini Live.",
        "es": "La carpeta de proveedores no está instalada: el asistente "
              "funciona con Gemini Live.",
        "pt": "A pasta de provedores não está instalada — o assistente "
              "funciona com o Gemini Live.",
        "fr": "Le dossier des fournisseurs n'est pas installé — l'assistant "
              "fonctionne avec Gemini Live.",
        "zh": "未安装服务商文件夹——助手将使用 Gemini Live 运行。",
    },
    "log.mic_muted": {
        "en": "Microphone muted.",
        "es": "Micrófono silenciado.",
        "pt": "Microfone mudo.",
        "fr": "Micro coupé.",
        "zh": "麦克风已静音。",
    },
    "log.mic_active": {
        "en": "Microphone active.",
        "es": "Micrófono activo.",
        "pt": "Microfone ativo.",
        "fr": "Micro actif.",
        "zh": "麦克风已开启。",
    },
    "log.initialised": {
        "en": "Initialised. Windows. {name} online.",
        "es": "Inicializado. Windows. {name} en línea.",
        "pt": "Inicializado. Windows. {name} online.",
        "fr": "Initialisé. Windows. {name} en ligne.",
        "zh": "已初始化。Windows。{name} 已上线。",
    },
    "log.video_sound_on": {
        "en": "The video's sound is on — silence it, close it, or press F4 to "
              "talk. Typing still works.",
        "es": "El sonido del vídeo está encendido: siléncialo, ciérralo o pulsa "
              "F4 para hablar. Escribir sigue funcionando.",
        "pt": "O som do vídeo está ligado — silencie-o, feche-o ou prima F4 "
              "para falar. Escrever continua a funcionar.",
        "fr": "Le son de la vidéo est activé — coupez-le, fermez-la ou appuyez "
              "sur F4 pour parler. La saisie fonctionne toujours.",
        "zh": "视频的声音已打开——请静音、关闭视频，或按 F4 说话。打字仍然可用。",
    },
    "log.video_quiet_again": {
        "en": "The video is quiet again.",
        "es": "El vídeo vuelve a estar en silencio.",
        "pt": "O vídeo está novamente em silêncio.",
        "fr": "La vidéo est de nouveau silencieuse.",
        "zh": "视频又安静下来了。",
    },
    "log.err_shortcut": {
        "en": "Shortcut failed — {error}",
        "es": "No se pudo crear el acceso directo: {error}",
        "pt": "Não foi possível criar o atalho — {error}",
        "fr": "Échec de la création du raccourci — {error}",
        "zh": "创建快捷方式失败——{error}",
    },
    "log.err_autostart": {
        "en": "Auto-start failed — {error}",
        "es": "El inicio automático falló: {error}",
        "pt": "O início automático falhou — {error}",
        "fr": "Échec du démarrage automatique — {error}",
        "zh": "开机自启失败——{error}",
    },
    "log.err_confirmation": {
        "en": "Confirmation failed — {error}",
        "es": "La confirmación falló: {error}",
        "pt": "A confirmação falhou — {error}",
        "fr": "Échec de la confirmation — {error}",
        "zh": "确认失败——{error}",
    },
    "log.err_config_save": {
        "en": "Config save failed — {error}",
        "es": "No se pudieron guardar los ajustes: {error}",
        "pt": "Não foi possível guardar as definições — {error}",
        "fr": "Échec de l'enregistrement des réglages — {error}",
        "zh": "保存设置失败——{error}",
    },
    "log.provider_settings_unavailable": {
        "en": "Provider settings unavailable: {error}",
        "es": "Ajustes de proveedores no disponibles: {error}",
        "pt": "Definições de provedores indisponíveis: {error}",
        "fr": "Réglages des fournisseurs indisponibles : {error}",
        "zh": "无法使用服务商设置：{error}",
    },
    "log.routine_not_changed": {
        "en": "Routine not changed — {error}",
        "es": "La rutina no cambió: {error}",
        "pt": "A rotina não foi alterada — {error}",
        "fr": "La routine n'a pas été modifiée — {error}",
        "zh": "例行任务未更改——{error}",
    },
    "log.quiz_score": {
        "en": "{topic} — {right}/{total} correct",
        "es": "{topic} — {right}/{total} correctos",
        "pt": "{topic} — {right}/{total} corretas",
        "fr": "{topic} — {right}/{total} correctes",
        "zh": "{topic} — 答对 {right}/{total}",
    },
    "log.file_loaded": {
        "en": "{name} ({size}) loaded",
        "es": "{name} ({size}) cargado",
        "pt": "{name} ({size}) carregado",
        "fr": "{name} ({size}) chargé",
        "zh": "已加载 {name}（{size}）",
    },
    "log.folder_loaded": {
        "en": "{name} ({count} files) loaded",
        "es": "{name} ({count} archivos) cargado",
        "pt": "{name} ({count} ficheiros) carregado",
        "fr": "{name} ({count} fichiers) chargé",
        "zh": "已加载 {name}（{count} 个文件）",
    },
    # -- appearance: the three faces of the assistant
    "appearance.title": {
        "en": "Appearance",
        "es": "Apariencia",
        "pt": "Aparência",
        "fr": "Apparence",
        "zh": "外观",
    },
    "appearance.dashboard": {
        "en": "Full dashboard",
        "es": "Panel completo",
        "pt": "Painel completo",
        "fr": "Tableau de bord complet",
        "zh": "完整面板",
    },
    "appearance.core": {
        "en": "Core only",
        "es": "Sólo el núcleo",
        "pt": "Apenas o núcleo",
        "fr": "Juste le cœur",
        "zh": "仅核心",
    },
    "appearance.tray": {
        "en": "In the tray",
        "es": "En la bandeja",
        "pt": "Na área de notificação",
        "fr": "Dans la zone de notification",
        "zh": "在托盘",
    },
    "appearance.corner": {
        "en": "Corner",
        "es": "Esquina",
        "pt": "Canto",
        "fr": "Coin",
        "zh": "角落",
    },
    "appearance.corner.bottom_right": {
        "en": "Bottom right",
        "es": "Abajo a la derecha",
        "pt": "Inferior direito",
        "fr": "En bas à droite",
        "zh": "右下",
    },
    "appearance.corner.bottom_left": {
        "en": "Bottom left",
        "es": "Abajo a la izquierda",
        "pt": "Inferior esquerdo",
        "fr": "En bas à gauche",
        "zh": "左下",
    },
    "appearance.corner.top_right": {
        "en": "Top right",
        "es": "Arriba a la derecha",
        "pt": "Superior direito",
        "fr": "En haut à droite",
        "zh": "右上",
    },
    "appearance.corner.top_left": {
        "en": "Top left",
        "es": "Arriba a la izquierda",
        "pt": "Superior esquerdo",
        "fr": "En haut à gauche",
        "zh": "左上",
    },
    "appearance.mode_tip": {
        "en": "How the assistant shows itself: the whole dashboard, only the "
              "core in a corner, or nothing but the icon in the tray.",
        "es": "Cómo se muestra el asistente: el panel completo, sólo el "
              "núcleo en una esquina, o nada más que el icono en la bandeja.",
        "pt": "Como o assistente se mostra: o painel completo, apenas o "
              "núcleo num canto, ou nada além do ícone na área de "
              "notificação.",
        "fr": "Comment l'assistant se montre : le tableau de bord complet, "
              "juste le cœur dans un coin, ou rien d'autre que l'icône dans "
              "la zone de notification.",
        "zh": "助手如何显示：完整面板、仅角落中的核心，或只有托盘图标。",
    },
    "appearance.corner_tip": {
        "en": "Where the core sits on the screen.",
        "es": "Dónde se sitúa el núcleo en la pantalla.",
        "pt": "Onde fica o núcleo no ecrã.",
        "fr": "Où se trouve le cœur à l'écran.",
        "zh": "核心在屏幕上的位置。",
    },
    # -- the tray icon
    "tray.window": {
        "en": "Window",
        "es": "Ventana",
        "pt": "Janela",
        "fr": "Fenêtre",
        "zh": "窗口",
    },
    "tray.maximize": {
        "en": "Maximize",
        "es": "Maximizar",
        "pt": "Maximizar",
        "fr": "Agrandir",
        "zh": "最大化",
    },
    "tray.exit": {
        "en": "Exit",
        "es": "Salir",
        "pt": "Sair",
        "fr": "Quitter",
        "zh": "退出",
    },
    "tray.hidden": {
        "en": "The assistant is in the tray. It still hears you.",
        "es": "El asistente está en la bandeja. Todavía te oye.",
        "pt": "O assistente está na área de notificação. Ainda ouve-o.",
        "fr": "L'assistant est dans la zone de notification. Il vous entend "
              "toujours.",
        "zh": "助手在托盘里。它仍然听得见你。",
    },
    # -- the hands: the cursor you steer with
    "controls.gesture": {
        "en": "HANDS: the gesture cursor",
        "es": "MANOS: cursor con gestos",
        "pt": "MÃOS: cursor por gestos",
        "fr": "MAINS: le curseur gestuel",
        "zh": "手势光标",
    },
    "controls.gesture_tip": {
        "en": "The camera watches your hands: the skeleton is drawn on the "
              "glass, the fingertip is the cursor and the pinch is a click.",
        "es": "La cámara vigila tus manos: el esqueleto se dibuja sobre el "
              "cristal, la punta del dedo es el cursor y el pinzamiento es un "
              "clic.",
        "pt": "A câmara vigia as suas mãos: o esqueleto desenha-se no "
              "cristal, a ponta do dedo é o cursor e o beliscar é um clique.",
        "fr": "La caméra surveille vos mains : le squelette est dessiné sur le "
              "verre, la pointe du doigt est le curseur et le pincement est un "
              "clic.",
        "zh": "摄像头注视你的手：骨架画在玻璃上，指尖是光标，捏合就是点击。",
    },
    "controls.gesture_pointer": {
        "en": "HANDS: the hand moves the pointer *",
        "es": "MANOS: la mano mueve el ratón *",
        "pt": "MÃOS: a mão move o rato *",
        "fr": "MAINS : la main bouge le pointeur *",
        "zh": "手势：手来移动指针 *",
    },
    "controls.gesture_pointer_needs_watch": {
        "en": "the camera is not watching the hands yet — turn that on first",
        "es": "la cámara todavía no está mirando las manos — activa eso primero",
        "pt": "a câmara ainda não está a ver as mãos — ativa isso primeiro",
        "fr": "la caméra ne regarde pas encore les mains — activez d'abord cela",
        "zh": "摄像头还没有在看手——先打开那个",
    },
    "controls.gesture_pointer_tip": {
        "en": "The hand takes the real pointer and the pinch clicks it. "
              "Marked with a star because it should only take the pointer "
              "while the assistant is listening — that rule is not written "
              "yet.",
        "es": "La mano toma el puntero real y el pinzamiento hace clic. "
              "Marcado con una estrella porque debería tomarlo sólo mientras "
              "el asistente escucha, y esa regla todavía no está escrita.",
        "pt": "A mão toma o ponteiro real e o beliscar clica. Marcado com uma "
              "estrela porque deveria tomá-lo só enquanto o assistente escuta, "
              "e essa regra ainda não está escrita.",
        "fr": "La main prend le vrai pointeur et le pincement clique. Marqué "
              "d'une étoile parce qu'il ne devrait le prendre que pendant que "
              "l'assistant écoute — cette règle n'est pas encore écrite.",
        "zh": "手接管真实指针，捏合即点击。标星号是因为它本应只在助手聆听时接管指针——这条规则还没写。",
    },
    "controls.gesture_advanced": {
        "en": "ADVANCED GESTURES", "es": "GESTOS AVANZADOS",
        "pt": "GESTOS AVANÇADOS", "fr": "GESTES AVANCÉS", "zh": "高级手势",
    },
    "controls.gesture_advanced_tip": {
        "en": "The gestures that change the desktop: switching windows, "
              "maximising, minimising, the screenshot, the thumb that approves "
              "and rejects, the L that wakes T.E.E.M.O. up and the clap that "
              "puts it to sleep. Off by default.",
        "es": "Los gestos que cambian el escritorio: cambiar de ventana, "
              "maximizar, minimizar, la captura, el pulgar que aprueba y "
              "rechaza, la L que despierta a T.E.E.M.O. y el choque de palmas "
              "que lo duerme. Desactivados por defecto.",
        "pt": "Os gestos que mudam a área de trabalho: trocar de janela, "
              "maximizar, minimizar, a captura, o polegar que aprova e rejeita, "
              "o L que acorda o T.E.E.M.O. e o bater de palmas que o adormece. "
              "Desativados por omissão.",
        "fr": "Les gestes qui changent le bureau : changer de fenêtre, "
              "agrandir, réduire, la capture, le pouce qui approuve et refuse, "
              "le L qui réveille T.E.E.M.O. et l'applaudissement qui l'endort. "
              "Désactivés par défaut.",
        "zh": "改变桌面的手势：切换窗口、最大化、最小化、截图、拇指的批准与拒绝、"
              "唤醒 T.E.E.M.O. 的 L 手势以及让它入睡的击掌。默认关闭。",
    },
    "controls.gesture_advanced_on": {
        "en": "the advanced gestures are armed",
        "es": "los gestos avanzados están activados",
        "pt": "os gestos avançados estão ativados",
        "fr": "les gestes avancés sont activés",
        "zh": "高级手势已启用",
    },
    "controls.gesture_advanced_off": {
        "en": "the advanced gestures are disarmed",
        "es": "los gestos avanzados están desactivados",
        "pt": "os gestos avançados estão desativados",
        "fr": "les gestes avancés sont désactivés",
        "zh": "高级手势已禁用",
    },
    "controls.pending": {
        "en": "* = not finished yet",
        "es": "* = todavía no terminado",
        "pt": "* = ainda não terminado",
        "fr": "* = pas encore terminé",
        "zh": "* = 尚未完成",
    },
    "controls.pending_tip": {
        "en": "A control marked with a star is there and works as far as it "
              "goes, but the rest of it is not written yet.",
        "es": "Un control marcado con una estrella está ahí y funciona hasta "
              "donde llega, pero el resto todavía no está escrito.",
        "pt": "Um controlo marcado com uma estrela está lá e funciona até onde "
              "chega, mas o resto ainda não está escrito.",
        "fr": "Un contrôle marqué d'une étoile est là et fonctionne jusqu'où "
              "il va, mais le reste n'est pas encore écrit.",
        "zh": "带星号的控件已经在那儿，也能用到现在这一步，但剩下的部分还没写。",
    },

    # -- ENGINE & PROVIDERS panel (gui/providers_panel.py) -------------------
    # The panel that decides what holds the conversation. Everything here is
    # something a person reads on screen, so everything here is a key: the
    # names of the providers and of the models are NOT, because those are
    # other people's names.
    "providers.title": {
        "en": "🔌  ENGINE  &  PROVIDERS",
        "es": "🔌  MOTOR  Y  PROVEEDORES",
        "pt": "🔌  MOTOR  E  PROVEDORES",
        "fr": "🔌  MOTEUR  ET  FOURNISSEURS",
        "zh": "🔌  引擎  与  服务商",
    },
    "providers.subtitle": {
        "en": "Choose what powers the conversation. Gemini is the default and "
              "needs nothing set up here.",
        "es": "Elige qué sostiene la conversación. Gemini es el "
              "predeterminado y no necesita nada configurado aquí.",
        "pt": "Escolha o que sustenta a conversa. O Gemini é o padrão e não "
              "precisa de nada configurado aqui.",
        "fr": "Choisissez ce qui porte la conversation. Gemini est l'option "
              "par défaut et n'a rien à configurer ici.",
        "zh": "选择支撑本次对话的引擎。Gemini 是默认选项，这里不需要配置任何东西。",
    },
    "providers.mode.gemini_body": {
        "en": "One connection carries your voice, its voice, the transcript "
              "and the tools together. The fastest option — about half a "
              "second to the first word.",
        "es": "Una sola conexión lleva tu voz, la suya, la transcripción y "
              "las herramientas a la vez. La opción más rápida: medio "
              "segundo hasta la primera palabra.",
        "pt": "Uma única ligação leva a sua voz, a dele, a transcrição e as "
              "ferramentas em conjunto. A opção mais rápida: cerca de meio "
              "segundo até a primeira palavra.",
        "fr": "Une seule connexion porte votre voix, la sienne, la "
              "transcription et les outils ensemble. L'option la plus rapide "
              ": environ une demi-seconde avant le premier mot.",
        "zh": "一条连接同时承载你的声音、它的声音、文字记录和工具。这是最快的选项——大约半秒后就能开口。",
    },
    "providers.mode.own": {
        "en": "🖥  MY OWN SETUP",
        "es": "🖥  MI PROPIA CONFIGURACIÓN",
        "pt": "🖥  A MINHA PRÓPRIA CONFIGURAÇÃO",
        "fr": "🖥  MA PROPRE CONFIGURATION",
        "zh": "🖥  我自己的配置",
    },
    "providers.mode.own_name": {
        "en": "MY OWN SETUP",
        "es": "MI PROPIA CONFIGURACIÓN",
        "pt": "A MINHA PRÓPRIA CONFIGURAÇÃO",
        "fr": "MA PROPRE CONFIGURATION",
        "zh": "我自己的配置",
    },
    "providers.mode.own_body": {
        "en": "Your choice of hearing, thinking and speaking. Can run entirely "
              "on this computer, with nothing leaving it. Slower: 1.5 to 4 "
              "seconds.",
        "es": "Tu elección de escucha, razonamiento y voz. Puede funcionar "
              "por completo en este equipo, sin que nada salga de él. Más "
              "lento: de 1,5 a 4 segundos.",
        "pt": "A sua escolha de audição, raciocínio e fala. Pode funcionar "
              "inteiramente neste computador, sem que nada saia dele. Mais "
              "lento: de 1,5 a 4 segundos.",
        "fr": "Votre choix d'écoute, de raisonnement et de parole. Peut "
              "tourner entièrement sur cet ordinateur, sans rien en sortir. "
              "Plus lent : de 1,5 à 4 secondes.",
        "zh": "由你来决定聆听、思考和朗读。可以完全在这台电脑上运行，不会有任何东西离开它。速度较慢：1.5 到 4 秒。",
    },
    # The three parts of a conversation. The heading is the name of the slot
    # and the help says what the slot does, in the words of somebody setting
    # it up rather than the words of the code.
    "providers.slot.hearing": {
        "en": "HEARING",
        "es": "ESCUCHA",
        "pt": "AUDIÇÃO",
        "fr": "ÉCOUTE",
        "zh": "听觉",
    },
    "providers.slot.hearing_help": {
        "en": "turns what you say into words",
        "es": "convierte lo que dices en palabras",
        "pt": "transforma o que você diz em palavras",
        "fr": "transforme ce que vous dites en mots",
        "zh": "把你说的话变成文字",
    },
    "providers.slot.hearing_word": {
        "en": "hearing",
        "es": "la escucha",
        "pt": "a audição",
        "fr": "l'écoute",
        "zh": "听觉",
    },
    "providers.slot.thinking": {
        "en": "THINKING",
        "es": "RAZONAMIENTO",
        "pt": "RACIOCÍNIO",
        "fr": "RAISONNEMENT",
        "zh": "思考",
    },
    "providers.slot.thinking_help": {
        "en": "understands, decides, uses your tools",
        "es": "entiende, decide y usa tus herramientas",
        "pt": "entende, decide e usa as suas ferramentas",
        "fr": "comprend, décide et utilise vos outils",
        "zh": "理解、决策并调用你的工具",
    },
    "providers.slot.thinking_word": {
        "en": "thinking",
        "es": "el razonamiento",
        "pt": "o raciocínio",
        "fr": "le raisonnement",
        "zh": "推理",
    },
    "providers.slot.speaking": {
        "en": "SPEAKING",
        "es": "HABLA",
        "pt": "FALA",
        "fr": "PAROLE",
        "zh": "朗读",
    },
    "providers.slot.speaking_help": {
        "en": "reads the answer out loud",
        "es": "lee la respuesta en voz alta",
        "pt": "lê a resposta em voz alta",
        "fr": "lit la réponse à voix haute",
        "zh": "把答案读出来",
    },
    "providers.slot.speaking_word": {
        "en": "speaking",
        "es": "la voz",
        "pt": "a voz",
        "fr": "la parole",
        "zh": "朗读",
    },
    # The two words of a yes/no answer live in ONE entry: "no" is spelled the
    # same in English and in Spanish, and a key is not allowed to say the
    # same thing in two languages. The bar separates them.
    "providers.bool_choices": {
        "en": "no|yes",
        "es": "no|sí",
        "pt": "não|sim",
        "fr": "non|oui",
        "zh": "否|是",
    },
    "providers.apply": {
        "en": "▸  SAVE  &&  APPLY",
        "es": "▸  GUARDAR  Y  APLICAR",
        "pt": "▸  SALVAR  E  APLICAR",
        "fr": "▸  ENREGISTRER  ET  APPLIQUER",
        "zh": "▸  保存并应用",
    },
    "providers.apply_label": {
        "en": "SAVE & APPLY",
        "es": "GUARDAR Y APLICAR",
        "pt": "SALVAR E APLICAR",
        "fr": "ENREGISTRER ET APPLIQUER",
        "zh": "保存并应用",
    },
    "providers.close": {
        "en": "CLOSE",
        "es": "CERRAR",
        "pt": "FECHAR",
        "fr": "FERMER",
        "zh": "关闭",
    },
    "providers.cancel": {
        "en": "CANCEL",
        "es": "CANCELAR",
        "pt": "CANCELAR",
        "fr": "ANNULER",
        "zh": "取消",
    },
    "providers.test": {
        "en": "TEST",
        "es": "PROBAR",
        "pt": "TESTAR",
        "fr": "TESTER",
        "zh": "测试",
    },
    "providers.refresh": {
        "en": "↻ REFRESH",
        "es": "↻ ACTUALIZAR",
        "pt": "↻ ATUALIZAR",
        "fr": "↻ ACTUALISER",
        "zh": "↻ 刷新",
    },
    "providers.more": {
        "en": "⌄ MORE",
        "es": "⌄ MÁS",
        "pt": "⌄ MAIS",
        "fr": "⌄ PLUS",
        "zh": "⌄ 更多",
    },
    "providers.less": {
        "en": "⌃ LESS",
        "es": "⌃ MENOS",
        "pt": "⌃ MENOS",
        "fr": "⌃ MOINS",
        "zh": "⌃ 更少",
    },
    "providers.install": {
        "en": "INSTALL {pkg}",
        "es": "INSTALAR {pkg}",
        "pt": "INSTALAR {pkg}",
        "fr": "INSTALLER {pkg}",
        "zh": "安装 {pkg}",
    },
    "providers.not_set": {
        "en": "— not set —",
        "es": "— sin configurar —",
        "pt": "— não definido —",
        "fr": "— non défini —",
        "zh": "— 未设置 —",
    },
    "providers.default_choice": {
        "en": "— default —",
        "es": "— predeterminado —",
        "pt": "— padrão —",
        "fr": "— par défaut —",
        "zh": "— 默认 —",
    },
    "providers.optional": {
        "en": "   (optional)",
        "es": "   (opcional)",
        "pt": "   (opcional)",
        "fr": "   (facultatif)",
        "zh": "   （可选）",
    },
    "providers.needs_install": {
        "en": "needs install",
        "es": "necesita instalación",
        "pt": "precisa de instalação",
        "fr": "nécessite une installation",
        "zh": "需要安装",
    },
    "providers.api_key_needed": {
        "en": "Needed — create one in your {name} account",
        "es": "Necesaria: crea una en tu cuenta de {name}",
        "pt": "Necessária — crie uma na sua conta {name}",
        "fr": "Nécessaire — créez-en une dans votre compte {name}",
        "zh": "需要——在你的 {name} 账户中创建一个",
    },
    "providers.gemini_body": {
        "en": "Nothing to set up. Your voice goes to Gemini and comes back as "
              "speech over one connection, with the transcript and every tool "
              "call on the same wire.",
        "es": "Nada que configurar. Tu voz va a Gemini y vuelve como voz por "
              "una sola conexión, con la transcripción y cada llamada a las "
              "herramientas en el mismo canal.",
        "pt": "Nada a configurar. A sua voz vai para o Gemini e volta como "
              "fala por uma única ligação, com a transcrição e cada chamada "
              "de ferramenta no mesmo canal.",
        "fr": "Rien à configurer. Votre voix part vers Gemini et revient en "
              "parole sur une seule connexion, avec la transcription et "
              "chaque appel d'outil sur le même fil.",
        "zh": "这里没有什么要设置的。你的声音送给 Gemini，再由它用声音送回，全部走同一条连接，文字记录和每一次工具调用也在上面。",
    },
    "providers.gemini_body2": {
        "en": "Pick {own} above to run it on your own machine, or through "
              "another company's model.",
        "es": "Elige {own} arriba para ejecutarlo en tu propio equipo, o a "
              "través del modelo de otra empresa.",
        "pt": "Escolha {own} acima para rodar na sua própria máquina, ou pelo "
              "modelo de outra empresa.",
        "fr": "Choisissez {own} ci-dessus pour le faire tourner sur votre "
              "propre machine, ou via le modèle d'une autre société.",
        "zh": "在上方选择{own}，即可在自己的电脑上运行，或通过其他公司的模型运行。",
    },
    "providers.sidecar.title": {
        "en": "⚡  BACKGROUND REQUESTS",
        "es": "⚡  SOLICITUDES EN SEGUNDO PLANO",
        "pt": "⚡  PEDIDOS EM SEGUNDO PLANO",
        "fr": "⚡  REQUÊTES EN ARRIÈRE-PLAN",
        "zh": "⚡  后台请求",
    },
    "providers.sidecar.name": {
        "en": "BACKGROUND REQUESTS",
        "es": "SOLICITUDES EN SEGUNDO PLANO",
        "pt": "PEDIDOS EM SEGUNDO PLANO",
        "fr": "REQUÊTES EN ARRIÈRE-PLAN",
        "zh": "后台请求",
    },
    "providers.sidecar.body": {
        "en": "Reading a document, looking at your screen, writing code — the "
              "assistant's own requests, not the conversation. These use "
              "Gemini's text quota, which runs out before the voice does. "
              "Sending them somewhere else keeps them working, and changes "
              "nothing you hear.",
        "es": "Leer un documento, mirar tu pantalla, escribir código: las "
              "peticiones del propio asistente, no la conversación. Usan la "
              "cuota de texto de Gemini, que se agota antes que la voz. "
              "Mandarlas a otro sitio las mantiene funcionando, y no cambia "
              "nada de lo que oyes.",
        "pt": "Ler um documento, olhar a sua tela, escrever código: os pedidos "
              "do próprio assistente, não a conversa. Usam a cota de texto do "
              "Gemini, que acaba antes do que a voz. Mandá-los para outro "
              "lugar os mantém funcionando, e não muda nada do que você "
              "ouve.",
        "fr": "Lire un document, regarder votre écran, écrire du code : les "
              "requêtes de l'assistant lui-même, pas la conversation. Elles "
              "utilisent le quota de texte de Gemini, qui s'épuise avant la "
              "voix. Les envoyer ailleurs les garde fonctionnelles, et ne "
              "change rien de ce que vous entendez.",
        "zh": "读文档、看屏幕、写代码——这些是助手自己的请求，不是对话。它们使用 "
              "Gemini 的文本额度，而文本额度比语音额度先用完。把它们送到别处"
              "就能继续工作，你听到的内容不会有任何改变。",
    },
    "providers.sidecar.fallback": {
        "en": "Fall back to {name}",
        "es": "Usar {name} como alternativa",
        "pt": "Usar {name} como alternativa",
        "fr": "Se rabattre sur {name}",
        "zh": "回退到 {name}",
    },
    "providers.sidecar.gemini_only": {
        "en": "Gemini only  —  the way it has always been",
        "es": "Solo Gemini  —  como siempre ha sido",
        "pt": "Somente Gemini  —  como sempre foi",
        "fr": "Gemini uniquement  —  comme toujours",
        "zh": "仅用 Gemini  ——  一如既往",
    },
    "providers.sidecar.try_gemini": {
        "en": "Try Gemini first  —  faster",
        "es": "Probar Gemini primero  —  más rápido",
        "pt": "Tentar o Gemini primeiro  —  mais rápido",
        "fr": "Essayer Gemini d'abord  —  plus rapide",
        "zh": "先用 Gemini  ——  更快",
    },
    "providers.sidecar.try_other": {
        "en": "Try the other one first  —  keeps more on your machine",
        "es": "Probar el otro primero  —  deja más cosas en tu equipo",
        "pt": "Tentar o outro primeiro  —  mantém mais coisas na sua máquina",
        "fr": "Essayer l'autre d'abord  —  garde plus de choses sur votre "
              "machine",
        "zh": "先用另一个  ——  更多内容留在本机",
    },
    "providers.uses_thinking": {
        "en": "Uses the model you chose for {slot}.",
        "es": "Usa el modelo que elegiste para {slot}.",
        "pt": "Usa o modelo que você escolheu para {slot}.",
        "fr": "Utilise le modèle choisi pour {slot}.",
        "zh": "使用你为{slot}选择的模型。",
    },
    "providers.sidecar.left_as_is": {
        "en": "Left as it is, these keep using Gemini and stop working when "
              "its text quota runs out.",
        "es": "Si lo dejas como está, estas siguen usando Gemini y dejan de "
              "funcionar cuando se agota su cuota de texto.",
        "pt": "Se ficar como está, estes continuam a usar o Gemini e deixam "
              "de funcionar quando a cota de texto acaba.",
        "fr": "Laissé tel quel, elles continuent d'utiliser Gemini et cessent "
              "de fonctionner quand son quota de texte est épuisé.",
        "zh": "保持原样时，这些请求继续使用 Gemini，并在其文本额度耗尽后停止工作。",
    },
    "providers.coding.title": {
        "en": "🛠  CODING",
        "es": "🛠  PROGRAMACIÓN",
        "pt": "🛠  PROGRAMAÇÃO",
        "fr": "🛠  PROGRAMMATION",
        "zh": "🛠  编程",
    },
    "providers.coding.body": {
        "en": "The model that plans, writes and fixes projects when you ask "
              "the assistant to build something. Saved to {path}.",
        "es": "El modelo que planifica, escribe y arregla proyectos cuando le "
              "pides al asistente que construya algo. Se guarda en {path}.",
        "pt": "O modelo que planeja, escreve e conserta projetos quando você "
              "pede ao assistente para construir alguma coisa. Salvo em "
              "{path}.",
        "fr": "Le modèle qui planifie, écrit et répare des projets quand vous "
              "demandez à l'assistant de construire quelque chose. Enregistré "
              "dans {path}.",
        "zh": "当你让助手搭建东西时，负责规划、编写和修复项目的模型。保存在 {path}。",
    },
    "providers.coding.recommended": {
        "en": "★ recommended",
        "es": "★ recomendado",
        "pt": "★ recomendado",
        "fr": "★ recommandé",
        "zh": "★ 推荐",
    },
    "providers.coding.with": {
        "en": "Code with {name}",
        "es": "Programar con {name}",
        "pt": "Programar com {name}",
        "fr": "Programmer avec {name}",
        "zh": "使用 {name} 编写代码",
    },
    "providers.coding.same_as_sidecar": {
        "en": "Same as background requests",
        "es": "Igual que las solicitudes en segundo plano",
        "pt": "Igual aos pedidos em segundo plano",
        "fr": "Identique aux requêtes en arrière-plan",
        "zh": "与后台请求相同",
    },
    "providers.coding.written_by_sidecar": {
        "en": "Code is written by whatever answers background requests above.",
        "es": "El código lo escribe lo que responde a las solicitudes en "
              "segundo plano de arriba.",
        "pt": "O código é escrito pelo que responde aos pedidos em segundo "
              "plano acima.",
        "fr": "Le code est écrit par ce qui répond aux requêtes en "
              "arrière-plan ci-dessus.",
        "zh": "代码由上面响应后台请求的那个模型编写。",
    },
    "providers.coding.uses_sidecar": {
        "en": "Uses the settings under {section}.",
        "es": "Usa los ajustes de {section}.",
        "pt": "Usa as configurações de {section}.",
        "fr": "Utilise les réglages de {section}.",
        "zh": "使用{section}下的设置。",
    },
    "providers.footer.gemini": {
        "en": "Gemini Live. Nothing on this panel changes the conversation.",
        "es": "Gemini Live. Nada en este panel cambia la conversación.",
        "pt": "Gemini Live. Nada neste painel muda a conversa.",
        "fr": "Gemini Live. Rien sur ce panneau ne change la conversation.",
        "zh": "Gemini Live。此面板上的任何设置都不会改变对话。",
    },
    "providers.footer.thinking_missing": {
        "en": "⚠  {slot} is not set — without it the assistant falls back to "
              "Gemini Live and says so in the log.",
        "es": "⚠  {slot} no está configurado: sin él el asistente recurre a "
              "Gemini Live y lo dice en el registro.",
        "pt": "⚠  {slot} não está definido: sem ele o assistente recorre ao "
              "Gemini Live e diz isso no registro.",
        "fr": "⚠  {slot} n'est pas défini — sans lui l'assistant se rabat sur "
              "Gemini Live et le signale dans le journal.",
        "zh": "⚠  {slot}尚未设置——没有它，助手会回退到 Gemini Live，并在日志中说明。",
    },
    "providers.footer.only_missing": {
        "en": "Only {slots} is missing — it will run, but you will type "
              "instead of speak, or read instead of listen.",
        "es": "Solo falta {slots}: funcionará, pero escribirás en vez de "
              "hablar, o leerás en vez de escuchar.",
        "pt": "Falta apenas {slots}: vai funcionar, mas você vai digitar em "
              "vez de falar, ou ler em vez de ouvir.",
        "fr": "Il ne manque que {slots} : ça fonctionnera, mais vous taperez "
              "au lieu de parler, ou lirez au lieu d'écouter.",
        "zh": "只缺少{slots}：仍然可以运行，但你只能打字而不是说话，或阅读而不是聆听。",
    },
    "providers.footer.all_set": {
        "en": "All three set. {apply} reconnects; the conversation is kept.",
        "es": "Los tres configurados. {apply} reconecta; la conversación se "
              "conserva.",
        "pt": "Os três definidos. {apply} reconecta; a conversa é mantida.",
        "fr": "Les trois sont définis. {apply} se reconnecte ; la conversation "
              "est conservée.",
        "zh": "三项都已设置。{apply}会重新连接；对话会保留。",
    },
    # The word that joins two things in a list. It is a key of its own
    # because Chinese joins without spaces and the others join with them.
    "providers.list_join": {
        "en": " and ",
        "es": " y ",
        "pt": " e ",
        "fr": " et ",
        "zh": "和",
    },
    "providers.status.checking": {
        "en": "checking…",
        "es": "comprobando…",
        "pt": "verificando…",
        "fr": "vérification…",
        "zh": "正在检查…",
    },
    "providers.status.stopping": {
        "en": "stopping…",
        "es": "deteniendo…",
        "pt": "parando…",
        "fr": "arrêt…",
        "zh": "正在停止…",
    },
    "providers.status.reading": {
        "en": "reading…",
        "es": "leyendo…",
        "pt": "lendo…",
        "fr": "lecture…",
        "zh": "正在读取…",
    },
    "providers.status.downloading": {
        "en": "downloading — {pct}%",
        "es": "descargando — {pct}%",
        "pt": "baixando — {pct}%",
        "fr": "téléchargement — {pct}%",
        "zh": "正在下载 — {pct}%",
    },
    "providers.status.nothing_to_check": {
        "en": "Nothing to check for this one.",
        "es": "No hay nada que comprobar en este.",
        "pt": "Não há nada a verificar neste.",
        "fr": "Rien à vérifier pour celui-ci.",
        "zh": "这一项没有什么可检查的。",
    },
    "providers.status.nothing_to_list": {
        "en": "Nothing to list for this one.",
        "es": "No hay nada que listar en este.",
        "pt": "Não há nada a listar neste.",
        "fr": "Rien à lister pour celui-ci.",
        "zh": "这一项没有什么可列出的。",
    },
    "providers.status.model_found": {
        "en": "✓  {count} model found on this server — pick one above.",
        "es": "✓  {count} modelo encontrado en este servidor: elige uno "
              "arriba.",
        "pt": "✓  {count} modelo encontrado neste servidor: escolha um "
              "acima.",
        "fr": "✓  {count} modèle trouvé sur ce serveur — choisissez-en un "
              "au-dessus.",
        "zh": "✓  在此服务器上找到 {count} 个模型——在上方选择一个。",
    },
    "providers.status.models_found": {
        "en": "✓  {count} models found on this server — pick one above.",
        "es": "✓  {count} modelos encontrados en este servidor: elige uno "
              "arriba.",
        "pt": "✓  {count} modelos encontrados neste servidor: escolha um "
              "acima.",
        "fr": "✓  {count} modèles trouvés sur ce serveur — choisissez-en un "
              "au-dessus.",
        "zh": "✓  在此服务器上找到 {count} 个模型——在上方选择一个。",
    },
    "providers.status.found": {
        "en": "✓  {count} found.",
        "es": "✓  {count} encontrados.",
        "pt": "✓  {count} encontrados.",
        "fr": "✓  {count} trouvés.",
        "zh": "✓  找到 {count} 个。",
    },
    "providers.status.none_found": {
        "en": "None found.",
        "es": "No se encontró nada.",
        "pt": "Não foi encontrado nada.",
        "fr": "Rien de trouvé.",
        "zh": "没有找到任何内容。",
    },
    "providers.status.available_list": {
        "en": "✓  {count} available: {items}",
        "es": "✓  {count} disponibles: {items}",
        "pt": "✓  {count} disponíveis: {items}",
        "fr": "✓  {count} disponibles : {items}",
        "zh": "✓  可用 {count} 个：{items}",
    },
    "providers.status.available": {
        "en": "✓  {count} available — pick one above.",
        "es": "✓  {count} disponibles: elige uno arriba.",
        "pt": "✓  {count} disponíveis: escolha um acima.",
        "fr": "✓  {count} disponibles — choisissez-en un au-dessus.",
        "zh": "✓  可用 {count} 个——在上方选择一个。",
    },
    "providers.status.installing": {
        "en": "installing {pkg} — this can take a minute…",
        "es": "instalando {pkg}: esto puede tardar un minuto…",
        "pt": "instalando {pkg} — isto pode levar um minuto…",
        "fr": "installation de {pkg} — cela peut prendre une minute…",
        "zh": "正在安装 {pkg}——这可能需要一分钟…",
    },
    "providers.status.installed": {
        "en": "✓  installed — close and reopen this panel",
        "es": "✓  instalado: cierra y vuelve a abrir este panel",
        "pt": "✓  instalado — feche e reabra este painel",
        "fr": "✓  installé — fermez puis rouvrez ce panneau",
        "zh": "✓  已安装——关闭并重新打开此面板",
    },
    "providers.status.pip_failed": {
        "en": "pip failed",
        "es": "pip falló",
        "pt": "o pip falhou",
        "fr": "pip a échoué",
        "zh": "pip 安装失败",
    },
}

CATALOG.update(_GUI_CATALOG)


# ── Translating ─────────────────────────────────────────────────────────────

class _MissingPlaceholder:
    """A placeholder the caller did not fill in.

    The ordinary behaviour of `str.format` is to raise. Here the missing
    name stays visible, because a sentence with a visible "{city}" is a
    sentence a human can still read, and a crash in the middle of drawing
    the window is not.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return "{" + self.name + "}"

    def __repr__(self) -> str:
        return self.__str__()

    def __format__(self, spec: str) -> str:
        return self.__str__()

    def __getitem__(self, item: str) -> "_MissingPlaceholder":
        return self


class _SafeValues(dict):
    """The mapping handed to `format_map`: unknown names stay as they are."""

    def __missing__(self, key):
        return _MissingPlaceholder(str(key))


def _safe_format(template: str, values: dict) -> str:
    """Substitute what we can, keep the rest, and never raise."""
    try:
        return str(template).format_map(_SafeValues(values))
    except Exception:
        return str(template)


def t(key: str, lang: str | None = None, /, **kwargs) -> str:
    """The text of a catalog key, in the given language.

    An unknown language falls back to English and an unknown key falls back
    to the key itself, so a missing translation shows up as a missing
    translation and not as a crash in the middle of the interface.
    """
    name = str(key or "")
    entry = CATALOG.get(name)
    if not entry:
        return name
    if lang is None:
        code = get_language()
    else:
        code = _normalize_lang(lang) or DEFAULT_LANGUAGE
    template = entry.get(code) or entry.get(DEFAULT_LANGUAGE) or ""
    if not template:
        return name
    if not kwargs:
        return template
    return _safe_format(template, kwargs)


def translate(text: str, lang: str | None = None) -> str:
    """Translate a WHOLE string, when that string is in the catalog.

    A tool that answers with a fixed sentence cannot ask for a key, so it
    sends the sentence itself; if that sentence is in the catalog it comes
    back translated, and if it is not it comes back unchanged.
    """
    raw = str(text or "")
    entry = CATALOG.get(raw)
    if not entry:
        return raw
    if lang is None:
        code = get_language()
    else:
        code = _normalize_lang(lang) or DEFAULT_LANGUAGE
    return entry.get(code) or entry.get(DEFAULT_LANGUAGE) or raw


def gesture_label(kind: str, raw: str, lang: str | None = None) -> str:
    """What the hand is doing, said in the language of the interface.

    The tracker speaks in small English words — "pinch", "double click",
    "right click" — because those are the names of the poses it can read.
    The catalogue has a key for each of them in the five languages, so the
    strip under the pictures says "clic derecho" and not "right click".

    A word the catalogue does not know comes back as it came: a missing
    translation shows up as the English word, never as an empty label.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    prefix = "gesture.pose." if str(kind) == "pose" else "gesture.action."
    entry = CATALOG.get(f"{prefix}{text.lower()}")
    if not entry:
        return text
    if lang is None:
        code = get_language()
    else:
        code = _normalize_lang(lang) or DEFAULT_LANGUAGE
    return entry.get(code) or entry.get(DEFAULT_LANGUAGE) or text


def catalog_keys() -> list[str]:
    """Every key of the catalog."""
    return list(CATALOG.keys())


def missing_translations(lang: str) -> list[str]:
    """The keys that have no text in that language."""
    code = _normalize_lang(lang) or ""
    return [
        key for key, entry in CATALOG.items()
        if not str(entry.get(code) or "").strip()
    ]


# ── The stored language ──────────────────────────────────────────────────────

def _stored_language() -> str:
    """The language the user CHOSE, as a code — or "" when they chose none.

    The RAW value, not `config_manager.get_language()`: that one answers
    "en" when nothing is stored, and a default that looks like a decision
    is exactly what has to be told apart from one.
    """
    try:
        raw = config_manager.load_api_keys().get("language") or ""
    except Exception:
        raw = ""
    return str(raw).strip().lower()


def get_language() -> str:
    """The language the product speaks, or the default one.

    THE ORDER MATTERS
        1. What the user CHOSE in the settings. That is a decision.
        2. The language they were last heard speaking, when nothing has been
           chosen yet. The owner of this machine speaks Spanish, and a
           product that greets them in English until somebody opens a
           settings panel has made the wrong assumption about who it is
           talking to.
        3. English, when neither is available.
    """
    stored = _stored_language()
    if stored in _LANGUAGE_CODES:
        return stored
    remembered = _remembered_language()
    if remembered:
        return remembered
    return DEFAULT_LANGUAGE


# The names the memory records the language under, and the code each one
# means. The memory stores the ENGLISH name of the language ("Spanish"), so
# this is the bridge from what the assistant noticed to what the interface
# and the catalogue speak.
_LANGUAGE_BY_NAME = {
    "english": "en",
    "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
    "portuguese": "pt", "português": "pt", "portugues": "pt", "português (Brasil)": "pt",
    "french": "fr", "français": "fr", "francais": "fr",
    "chinese": "zh", "mandarin": "zh", "中文": "zh", "简体中文": "zh",
}


def _remembered_language() -> str:
    """The language this person was last heard speaking, as a code."""
    try:
        from memory.memory_manager import load_memory
        memory = load_memory() or {}
        entry = (memory.get("identity") or {}).get("language")
        if isinstance(entry, dict):
            value = str(entry.get("value") or "").strip().lower()
        else:
            value = str(entry or "").strip().lower()
        return _LANGUAGE_BY_NAME.get(value, "")
    except Exception:
        return ""


def set_language(code: str) -> bool:
    """Store the language. True when it changed, False when it did not.

    The comparison is against the STORED value, not against the effective
    one: choosing the language the person was already speaking is still a
    choice — it is the difference between "the assistant noticed" and "the
    user asked for this".
    """
    normalized = str(code or "").strip().lower()
    if normalized not in _LANGUAGE_CODES:
        return False
    current = _stored_language()
    config_manager.save_language(normalized)
    return normalized != current


# ── The region ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Region:
    """Where the user is: a city, its country and, when we know it, where
    exactly that city is."""

    city: str
    country: str
    country_code: str
    latitude: float
    longitude: float


DEFAULT_REGION = Region("Lima", "Peru", "PE", -12.0464, -77.0428)

REGIONS: tuple[Region, ...] = (
    Region("Tacna", "Peru", "PE", -18.0146, -70.2536),
    Region("Lima", "Peru", "PE", -12.0464, -77.0428),
    Region("La Libertad", "Peru", "PE", -8.1089, -78.9594),
    Region("Arequipa", "Peru", "PE", -16.4090, -71.5375),
    Region("La Paz", "Bolivia", "BO", -16.4897, -68.1193),
    Region("Bogota", "Colombia", "CO", 4.7110, -74.0721),
    Region("Medellin", "Colombia", "CO", 6.2442, -75.5812),
    Region("Arica", "Chile", "CL", -18.4783, -70.3126),
    Region("Santiago", "Chile", "CL", -33.4489, -70.6693),
    Region("Paris", "France", "FR", 48.8566, 2.3522),
    Region("Madrid", "Spain", "ES", 40.4168, -3.7038),
    Region("London", "United Kingdom", "GB", 51.5074, -0.1278),
    Region("Mexico City", "Mexico", "MX", 19.4326, -99.1332),
    Region("Buenos Aires", "Argentina", "AR", -34.6037, -58.3816),
    Region("Sao Paulo", "Brazil", "BR", -23.5505, -46.6333),
    Region("Lisbon", "Portugal", "PT", 38.7223, -9.1393),
    Region("Berlin", "Germany", "DE", 52.5200, 13.4050),
    Region("New York", "United States", "US", 40.7128, -74.0060),
    Region("Tokyo", "Japan", "JP", 35.6762, 139.6503),
)


def regions() -> list[Region]:
    """Every region the product knows."""
    return list(REGIONS)


def _fold(text: str) -> str:
    """A comparable form of a name: no accents, no case, no spaces.

    "Perú", "peru" and "PERU" are the same country to a person typing in
    a hurry, and the same country to the weather.
    """
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    without_accents = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    return "".join(ch for ch in without_accents.casefold() if ch.isalnum())


def _country_matches(region: Region, folded_country: str) -> bool:
    """Whether that folded name is this region's country."""
    if not folded_country:
        return True
    if folded_country == _fold(region.country):
        return True
    return bool(region.country_code) and folded_country == _fold(region.country_code)


def _match_region(city: str, country: str | None) -> Region | None:
    """The region of that table that answers to that city and country."""
    city_key = _fold(city)
    if not city_key:
        return None
    country_key = _fold(country) if country else ""
    for region in REGIONS:
        if _fold(region.city) != city_key:
            continue
        if not _country_matches(region, country_key):
            continue
        return region
    return None


def get_region() -> Region:
    """The region the user chose, or the default one."""
    try:
        stored = config_manager.get_region() or {}
    except Exception:
        stored = {}
    city = str(stored.get("city") or "").strip()
    country = str(stored.get("country") or "").strip()
    if not city and not country:
        return DEFAULT_REGION
    matched = _match_region(city, country or None)
    if matched is not None:
        return matched
    return Region(city=city, country=country, country_code="",
                  latitude=0.0, longitude=0.0)


def set_region(city: str, country: str | None = None) -> Region:
    """Store the region and return it.

    A city that is not in the table is still accepted: the person may live
    somewhere the product has never heard of, and refusing their answer
    would leave the product with a region that is not theirs.
    """
    name = str(city or "").strip()
    country_name = str(country or "").strip()
    matched = _match_region(name, country_name or None)
    if matched is not None:
        region = matched
    else:
        region = Region(city=name, country=country_name, country_code="",
                        latitude=0.0, longitude=0.0)
    config_manager.save_region(region.city, region.country)
    return region


def region_label(region: Region | None = None) -> str:
    """The region as it is shown to a person: "Tacna, Peru"."""
    target = region if region is not None else get_region()
    city = str(target.city or "").strip()
    country = str(target.country or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country


def region_prompt_line() -> str:
    """One English sentence for the model: the language, and the place.

    It is written for the MODEL, which reads English prompts, so it names
    the language in its own language and the region in its own label.
    """
    language = _language_object(get_language())
    region = get_region()
    return (
        f"The user has chosen {language.name_native} as the interface and "
        f"conversation language. The user's region is {region_label(region)}: "
        "use it for local weather, national and regional news."
    )


def language_prompt_line() -> str:
    """One English sentence for the model: answer in one language only."""
    language = _language_object(get_language())
    return (
        f"The user's region is {region_label()}. Reply entirely in "
        f"{language.name_native}, in every message, and never mix languages."
    )
