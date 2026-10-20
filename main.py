"""
main.py - Botcord
Discord-bot console. Chat-app layout inspired by Discord (server rail + channel
panel + chat) with its own look. Realtime via the official Gateway.

Loading model
  * servers   - fetched in pages, shown as they arrive, rail/panel built in chunks
  * messages  - newest page first ("Loading messages..."), older pages load
                automatically when you scroll to the top
  * sending   - optimistic: your message appears instantly and is confirmed
                (or marked failed, with retry) when Discord answers
"""

import os
import re
import time
import threading
import datetime
import requests

from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import (StringProperty, BooleanProperty, ListProperty,
                             NumericProperty, ObjectProperty)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import AsyncImage
from kivy.utils import escape_markup, get_color_from_hex
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel, MDIcon  # noqa: F401  (registered for KV)
from kivymd.uix.list import OneLineListItem
from kivymd.uix.spinner import MDSpinner  # noqa: F401  (registered for KV)
from kivymd.uix.textfield import MDTextField

try:
    from kivymd.toast import toast
except Exception:  # pragma: no cover
    def toast(msg, *a, **k):
        print("toast:", msg)

import accounts_manager as am
import discord_api as api
from gateway import Gateway

PAGE = 30            # messages per page (initial + each "older" page)

KV = """
#:import get_color_from_hex kivy.utils.get_color_from_hex
#:set C_BG get_color_from_hex("#313338")
#:set C_RAIL get_color_from_hex("#1E1F22")
#:set C_PANEL get_color_from_hex("#2B2D31")
#:set C_BAR get_color_from_hex("#232428")
#:set C_SURF get_color_from_hex("#383A40")
#:set C_SURF2 get_color_from_hex("#404249")
#:set C_TEXT get_color_from_hex("#F2F3F5")
#:set C_MUTED get_color_from_hex("#949BA4")
#:set C_ACCENT get_color_from_hex("#5865F2")
#:set C_ONACC (1, 1, 1, 1)
#:set C_GREEN get_color_from_hex("#23A55A")
#:set C_WARN get_color_from_hex("#F0B232")
#:set C_DANGER get_color_from_hex("#F23F43")

<T@MDLabel>:
    theme_text_color: "Custom"
    text_color: C_TEXT
<TM@MDLabel>:
    theme_text_color: "Custom"
    text_color: C_MUTED
<IBtn@MDIconButton>:
    theme_icon_color: "Custom"
    icon_color: C_TEXT
<Ico@MDIcon>:
    theme_text_color: "Custom"
    text_color: C_MUTED
<RailSep@Widget>:
    size_hint_y: None
    height: dp(12)
    canvas:
        Color:
            rgba: C_SURF
        RoundedRectangle:
            pos: self.center_x - dp(16), self.center_y - dp(1)
            size: dp(32), dp(2)
            radius: [dp(1)]
<TopBar@BoxLayout>:
    size_hint_y: None
    height: dp(56)
    padding: dp(6), 0
    spacing: dp(6)
    canvas.before:
        Color:
            rgba: C_BG
        Rectangle:
            pos: self.pos
            size: self.size
    canvas.after:
        Color:
            rgba: C_RAIL
        Rectangle:
            pos: self.x, self.y
            size: self.width, dp(1)
<Banner@BoxLayout>:
    size_hint_y: None
    padding: dp(14), 0
    canvas.before:
        Color:
            rgba: C_SURF2
        Rectangle:
            pos: self.pos
            size: self.size

# circular avatar: tinted disc + letter, image fades in on top once loaded
<Avatar>:
    size_hint: None, None
    size: dp(40), dp(40)
    canvas.before:
        StencilPush
        Ellipse:
            pos: self.pos
            size: self.size
        StencilUse
        Color:
            rgba: root.tint
        Ellipse:
            pos: self.pos
            size: self.size
    canvas.after:
        StencilUnUse
        Ellipse:
            pos: self.pos
            size: self.size
        StencilPop
    MDLabel:
        text: root.letter
        halign: "center"
        valign: "center"
        bold: True
        theme_text_color: "Custom"
        text_color: 1, 1, 1, 1

# rounded server icon used on the rail
<IconTile>:
    size_hint: None, None
    size: dp(48), dp(48)
    canvas.before:
        StencilPush
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [root.corner]
        StencilUse
    canvas.after:
        StencilUnUse
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [root.corner]
        StencilPop

<RailItem>:
    size_hint: 1, None
    height: dp(58)
    canvas.before:
        Color:
            rgba: C_ACCENT if root.active else root.tint
        RoundedRectangle:
            pos: self.center_x - dp(24), self.center_y - dp(24)
            size: dp(48), dp(48)
            radius: [dp(16) if root.active else dp(24)]
        Color:
            rgba: C_TEXT if (root.active or root.unread) else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.x - dp(4), self.center_y - (dp(20) if root.active else dp(4))
            size: dp(8), (dp(40) if root.active else dp(8))
            radius: [dp(4)]
    MDLabel:
        text: root.text
        halign: "center"
        valign: "center"
        bold: True
        opacity: 0 if root.icon else 1
        theme_text_color: "Custom"
        text_color: C_ONACC
    MDIcon:
        icon: root.icon or "blank"
        halign: "center"
        pos_hint: {"center_x": .5, "center_y": .5}
        opacity: 1 if root.icon else 0
        theme_text_color: "Custom"
        text_color: C_ONACC if root.active else C_TEXT

<ChannelRow>:
    orientation: "horizontal"
    size_hint_y: None
    height: dp(40)
    padding: dp(14), 0, dp(14), 0
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: C_SURF if root.active else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.x + dp(8), self.y + dp(2)
            size: self.width - dp(16), self.height - dp(4)
            radius: [dp(8)]
    canvas.after:
        Color:
            rgba: C_TEXT if root.unread else (0, 0, 0, 0)
        Ellipse:
            pos: self.x + dp(2), self.center_y - dp(4)
            size: dp(8), dp(8)
    Ico:
        icon: root.icon
        size_hint_x: None
        width: dp(22)
        text_color: C_TEXT if (root.active or root.unread) else C_MUTED
    T:
        text: root.text
        bold: root.unread
        shorten: True
        text_color: C_TEXT if (root.active or root.unread) else C_MUTED
    TM:
        text: root.sub
        halign: "right"
        size_hint_x: None
        width: dp(30)
        font_style: "Caption"

<CategoryRow>:
    orientation: "horizontal"
    size_hint_y: None
    height: dp(34)
    padding: dp(12), dp(6), dp(10), 0
    spacing: dp(2)
    Ico:
        icon: "chevron-right" if root.collapsed else "chevron-down"
        size_hint_x: None
        width: dp(20)
    TM:
        text: root.text
        font_style: "Caption"
        bold: True
        shorten: True

# "Loading ..." / "Beginning of ..." strip used in lists and at the top of chats
<TopStrip>:
    orientation: "horizontal"
    size_hint_y: None
    height: dp(44) if root.text else 0
    opacity: 1 if root.text else 0
    padding: dp(14), dp(10)
    spacing: dp(10)
    Widget:
    MDSpinner:
        size_hint: None, None
        size: dp(18), dp(18)
        pos_hint: {"center_y": .5}
        active: root.busy
        opacity: 1 if root.busy else 0
    TM:
        text: root.text
        font_style: "Caption"
        adaptive_width: True
    Widget:

<VoiceRow>:
    size_hint_y: None
    height: dp(30)
    padding: dp(40), 0, dp(14), 0
    spacing: dp(8)
    Avatar:
        size: dp(22), dp(22)
        pos_hint: {"center_y": .5}
        letter: root.letter
        src: root.src
        tint: root.tint
    TM:
        text: root.name
        font_style: "Caption"
        shorten: True
    Ico:
        icon: "microphone-off"
        size_hint_x: None
        width: dp(18)
        opacity: 1 if root.muted else 0
    Ico:
        icon: "headphones-off"
        size_hint_x: None
        width: dp(18)
        opacity: 1 if root.deaf else 0

<VoiceTile>:
    size_hint_y: None
    height: dp(64)
    padding: dp(12), dp(10)
    spacing: dp(12)
    canvas.before:
        Color:
            rgba: C_SURF
        RoundedRectangle:
            pos: self.x + dp(10), self.y + dp(3)
            size: self.width - dp(20), self.height - dp(6)
            radius: [dp(14)]
    Widget:
        size_hint_x: None
        width: dp(4)
    Avatar:
        size: dp(42), dp(42)
        pos_hint: {"center_y": .5}
        letter: root.letter
        src: root.src
        tint: root.tint
    T:
        text: root.name
    Ico:
        icon: "broadcast"
        size_hint_x: None
        width: dp(24)
        opacity: 1 if root.live else 0
        text_color: C_GREEN
    Ico:
        icon: "video"
        size_hint_x: None
        width: dp(24)
        opacity: 1 if root.video else 0
        text_color: C_GREEN
    Ico:
        icon: "microphone-off"
        size_hint_x: None
        width: dp(24)
        opacity: 1 if root.muted else 0
        text_color: C_DANGER if root.srv else C_MUTED
    Ico:
        icon: "headphones-off"
        size_hint_x: None
        width: dp(24)
        opacity: 1 if root.deaf else 0
        text_color: C_DANGER if root.srv else C_MUTED
    Widget:
        size_hint_x: None
        width: dp(4)

<AccountRow>:
    size_hint_y: None
    height: dp(72)
    padding: dp(20), dp(10), dp(8), dp(10)
    spacing: dp(14)
    canvas.before:
        Color:
            rgba: C_SURF
        RoundedRectangle:
            pos: self.x + dp(10), self.y + dp(4)
            size: self.width - dp(20), self.height - dp(8)
            radius: [dp(16)]
    Avatar:
        size: dp(46), dp(46)
        pos_hint: {"center_y": .5}
        letter: root.letter
        src: root.src
        tint: root.tint
    BoxLayout:
        orientation: "vertical"
        T:
            text: root.name
            bold: True
        TM:
            text: root.sub
            font_style: "Caption"
    IBtn:
        icon: "trash-can-outline"
        icon_color: C_DANGER
        pos_hint: {"center_y": .5}
        on_release: root.on_remove()

<JumpPill>:
    orientation: "horizontal"
    size_hint: None, None
    size: dp(160), dp(38)
    padding: dp(12), 0, dp(14), 0
    spacing: dp(6)
    canvas.before:
        Color:
            rgba: C_ACCENT
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(19)]
    Ico:
        icon: "arrow-down"
        size_hint_x: None
        width: dp(20)
        text_color: C_ONACC
    T:
        text: "Jump to latest"
        font_style: "Caption"
        bold: True
        text_color: C_ONACC

<MsgRow>:
    orientation: "vertical"
    size_hint_y: None
    height: dv.height + bd.height
    opacity: 0.55 if root.state == "sending" else 1
    BoxLayout:
        id: dv
        size_hint_y: None
        height: dp(32) if root.day else 0
        opacity: 1 if root.day else 0
        padding: dp(14), dp(8)
        spacing: dp(10)
        Widget:
            canvas:
                Color:
                    rgba: C_SURF2
                Rectangle:
                    pos: self.x, self.center_y
                    size: self.width, dp(1)
        TM:
            text: root.day
            font_style: "Caption"
            bold: True
            adaptive_width: True
        Widget:
            canvas:
                Color:
                    rgba: C_SURF2
                Rectangle:
                    pos: self.x, self.center_y
                    size: self.width, dp(1)
    BoxLayout:
        id: bd
        orientation: "horizontal"
        size_hint_y: None
        height: txt.height + self.padding[1] + self.padding[3]
        padding: dp(14), (dp(10) if root.first else dp(1)), dp(14), dp(1)
        spacing: dp(12)
        BoxLayout:
            orientation: "vertical"
            size_hint_x: None
            width: dp(40)
            Avatar:
                opacity: 1 if root.first else 0
                letter: root.letter
                src: root.src
                tint: root.tint
            Widget:
        BoxLayout:
            id: txt
            orientation: "vertical"
            size_hint_y: None
            height: self.minimum_height
            BoxLayout:
                orientation: "horizontal"
                size_hint_y: None
                height: dp(18) if root.reply else 0
                opacity: 1 if root.reply else 0
                spacing: dp(4)
                Ico:
                    icon: "reply"
                    size_hint_x: None
                    width: dp(16)
                TM:
                    text: root.reply
                    font_style: "Caption"
                    shorten: True
            T:
                text: root.header
                markup: True
                size_hint_y: None
                height: dp(22) if root.first else 0
                opacity: 1 if root.first else 0
            T:
                text: root.body
                adaptive_height: True
                text_color: C_DANGER if root.state == "failed" else C_TEXT
            AsyncImage:
                source: root.img
                fit_mode: "contain"
                size_hint_y: None
                height: dp(190) if root.img else 0
                opacity: 1 if root.img else 0
            TM:
                text: "Failed to send - long-press to retry or discard"
                font_style: "Caption"
                size_hint_y: None
                height: dp(18) if root.state == "failed" else 0
                opacity: 1 if root.state == "failed" else 0
                text_color: C_DANGER


ScreenManager:
    LoginScreen:
    AccountsScreen:
    HomeScreen:
    ChatScreen:
    VoiceScreen:


<LoginScreen@MDScreen>:
    name: "login"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: C_BG
        padding: dp(28)
        spacing: dp(14)
        Widget:
            size_hint_y: .6
        T:
            text: "botcord"
            font_style: "H3"
            bold: True
            halign: "center"
            adaptive_height: True
        TM:
            text: "Control a Discord bot from your phone"
            halign: "center"
            adaptive_height: True
        Widget:
            size_hint_y: None
            height: dp(10)
        MDTextField:
            id: token_field
            hint_text: "Bot token"
            password: True
            mode: "fill"
            fill_color_normal: C_SURF
            fill_color_focus: C_SURF2
            text_color_normal: C_TEXT
            text_color_focus: C_TEXT
            line_color_focus: C_ACCENT
            hint_text_color_focus: C_ACCENT
        MDRaisedButton:
            text: "LOG IN"
            md_bg_color: C_ACCENT
            text_color: C_ONACC
            size_hint_x: 1
            on_release: app.try_login(token_field.text)
        MDFlatButton:
            text: "SAVED ACCOUNTS"
            theme_text_color: "Custom"
            text_color: C_MUTED
            pos_hint: {"center_x": .5}
            on_release: app.show_accounts()
        TM:
            text: "Tip: in the Developer Portal turn on Message Content Intent so you can read messages in servers."
            font_style: "Caption"
            halign: "center"
            adaptive_height: True
        Widget:
            size_hint_y: 1

<AccountsScreen@MDScreen>:
    name: "accounts"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: C_BG
        TopBar:
            Widget:
                size_hint_x: None
                width: dp(12)
            T:
                text: "Accounts"
                bold: True
                font_style: "H6"
            IBtn:
                icon: "plus"
                icon_color: C_ACCENT
                on_release: app.goto("login")
        ScrollView:
            MDBoxLayout:
                id: accounts_list
                orientation: "vertical"
                adaptive_height: True
                padding: 0, dp(8)

<HomeScreen@MDScreen>:
    name: "home"
    MDBoxLayout:
        orientation: "horizontal"
        md_bg_color: C_RAIL
        BoxLayout:
            orientation: "vertical"
            size_hint_x: None
            width: dp(72)
            ScrollView:
                do_scroll_x: False
                bar_width: 0
                MDBoxLayout:
                    id: rail_box
                    orientation: "vertical"
                    adaptive_height: True
                    padding: 0, dp(8)
            RailItem:
                icon: "account-switch"
                tint: C_SURF
                on_release: app.show_accounts()
        BoxLayout:
            orientation: "vertical"
            canvas.before:
                Color:
                    rgba: C_PANEL
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(20), 0, 0, 0]
            BoxLayout:
                size_hint_y: None
                height: dp(52)
                padding: dp(18), 0, dp(10), 0
                canvas.after:
                    Color:
                        rgba: C_RAIL
                    Rectangle:
                        pos: self.x, self.y
                        size: self.width, dp(1)
                T:
                    id: panel_title
                    text: ""
                    bold: True
                    font_style: "Subtitle1"
                    shorten: True
            ScrollView:
                do_scroll_x: False
                bar_width: dp(2)
                MDBoxLayout:
                    id: panel_box
                    orientation: "vertical"
                    adaptive_height: True
                    padding: 0, dp(4), 0, dp(16)
            BoxLayout:
                size_hint_y: None
                height: dp(58)
                padding: dp(12), dp(8), dp(4), dp(8)
                spacing: dp(10)
                canvas.before:
                    Color:
                        rgba: C_BAR
                    Rectangle:
                        pos: self.pos
                        size: self.size
                Avatar:
                    size: dp(38), dp(38)
                    pos_hint: {"center_y": .5}
                    letter: app.me_letter
                    src: app.me_src
                    tint: app.me_tint
                BoxLayout:
                    orientation: "vertical"
                    padding: 0, dp(3)
                    T:
                        text: app.me_name
                        bold: True
                        shorten: True
                        font_style: "Subtitle2"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(16)
                        spacing: dp(6)
                        Widget:
                            size_hint: None, None
                            size: dp(8), dp(8)
                            pos_hint: {"center_y": .5}
                            canvas:
                                Color:
                                    rgba: C_GREEN if app.gw_state == "live" else (C_DANGER if app.gw_state == "error" else C_WARN)
                                Ellipse:
                                    pos: self.pos
                                    size: self.size
                        TM:
                            text: app.gw_text
                            font_style: "Caption"
                IBtn:
                    icon: "tune-variant"
                    pos_hint: {"center_y": .5}
                    on_release: app.status_dialog()

<ChatScreen@MDScreen>:
    name: "chat"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: C_BG
        TopBar:
            IBtn:
                icon: "arrow-left"
                on_release: app.back()
            Ico:
                id: chat_icon
                icon: "pound"
                size_hint_x: None
                width: dp(22)
            BoxLayout:
                orientation: "vertical"
                padding: 0, dp(8)
                T:
                    id: chat_title
                    text: ""
                    bold: True
                    font_style: "Subtitle1"
                    shorten: True
                TM:
                    id: chat_sub
                    text: ""
                    font_style: "Caption"
                    shorten: True
        Banner:
            height: dp(32) if app.notice else 0
            opacity: 1 if app.notice else 0
            TM:
                text: app.notice
                font_style: "Caption"
        FloatLayout:
            ScrollView:
                id: scroll
                do_scroll_x: False
                bar_width: dp(3)
                opacity: 0 if app.loading_msgs else 1
                on_scroll_y: app.scroll_moved(self, self.scroll_y)
                on_height: app.view_resized()
                MDBoxLayout:
                    id: messages_box
                    orientation: "vertical"
                    adaptive_height: True
                    padding: 0, dp(8)
            BoxLayout:
                id: status_box
                orientation: "vertical"
                size_hint: None, None
                size: dp(240), dp(110)
                pos_hint: {"center_x": .5, "center_y": .5}
                spacing: dp(10)
                opacity: 1 if app.chat_status else 0
                MDSpinner:
                    size_hint: None, None
                    size: dp(34), dp(34)
                    pos_hint: {"center_x": .5}
                    active: app.loading_msgs
                    opacity: 1 if app.loading_msgs else 0
                TM:
                    text: app.chat_status
                    halign: "center"
                    font_style: "Subtitle2"
                MDFlatButton:
                    text: "RETRY"
                    theme_text_color: "Custom"
                    text_color: C_ACCENT
                    pos_hint: {"center_x": .5}
                    opacity: 1 if app.chat_error else 0
                    disabled: not app.chat_error
                    on_release: app.retry_load()
            JumpPill:
                pos_hint: {"center_x": .5, "y": .03}
                opacity: 1 if app.show_jump else 0
                disabled: not app.show_jump
                on_release: app.jump_latest()
        TM:
            text: app.typing_text
            font_style: "Caption"
            size_hint_y: None
            height: dp(18)
            padding: dp(16), 0
        Banner:
            height: dp(38) if app.bar_text else 0
            opacity: 1 if app.bar_text else 0
            padding: dp(14), 0, dp(2), 0
            Ico:
                icon: app.bar_icon or "blank"
                size_hint_x: None
                width: dp(22)
            TM:
                text: app.bar_text
                font_style: "Caption"
                shorten: True
            IBtn:
                icon: "close"
                on_release: app.clear_bar()
        BoxLayout:
            size_hint_y: None
            height: wrap.height + dp(14)
            padding: dp(10), dp(7), dp(6), dp(7)
            spacing: dp(6)
            canvas.before:
                Color:
                    rgba: C_BG
                Rectangle:
                    pos: self.pos
                    size: self.size
            BoxLayout:
                id: wrap
                size_hint_y: None
                height: msg_input.height
                canvas.before:
                    Color:
                        rgba: C_SURF
                    RoundedRectangle:
                        pos: self.pos
                        size: self.size
                        radius: [dp(21)]
                TextInput:
                    id: msg_input
                    hint_text: "Message"
                    multiline: True
                    size_hint_y: None
                    height: min(max(self.minimum_height, dp(42)), dp(120))
                    background_normal: ""
                    background_active: ""
                    background_color: 0, 0, 0, 0
                    foreground_color: C_TEXT
                    hint_text_color: C_MUTED
                    cursor_color: C_ACCENT
                    padding: dp(16), dp(11)
                    on_text: app.on_typing(self.text)
            IBtn:
                icon: "send"
                icon_color: C_ACCENT if msg_input.text.strip() else C_MUTED
                pos_hint: {"center_y": .5}
                on_release: app.send_current()

<VoiceScreen@MDScreen>:
    name: "voice"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: C_BG
        TopBar:
            IBtn:
                icon: "arrow-left"
                on_release: app.back()
            Ico:
                id: voice_icon
                icon: "volume-high"
                size_hint_x: None
                width: dp(22)
            BoxLayout:
                orientation: "vertical"
                padding: 0, dp(8)
                T:
                    id: voice_title
                    text: ""
                    bold: True
                    font_style: "Subtitle1"
                TM:
                    id: voice_count
                    text: ""
                    font_style: "Caption"
        Banner:
            height: dp(64)
            TM:
                text: "Live presence only. Discord requires end-to-end encrypted (DAVE) audio for every voice connection, which this build can't do, so no audio is sent or played."
                font_style: "Caption"
        ScrollView:
            do_scroll_x: False
            MDBoxLayout:
                id: voice_box
                orientation: "vertical"
                adaptive_height: True
                padding: 0, dp(10)
"""

# avatar background colours and (lighter) matching name colours, index-aligned
AV_COLORS = ["#5865F2", "#3BA55D", "#D9941A", "#ED4245", "#EB459E", "#4F7D9E", "#2BA8C9", "#8B5CF6"]
NAME_COLORS = ["#9AA8FF", "#6FD99B", "#FFC15E", "#FF8A8C", "#FF8FCB", "#9BB3C9", "#7FD6F0", "#C4A8FF"]
SYSTEM_TEXT = {6: "pinned a message to this channel.", 7: "joined the server.",
               8: "boosted the server.", 18: "started a thread."}

_RE_USER = re.compile(r"<@!?(\d+)>")
_RE_ROLE = re.compile(r"<@&(\d+)>")
_RE_CHAN = re.compile(r"<#(\d+)>")
_RE_EMOJI = re.compile(r"<a?:(\w+):\d+>")


def _hash(key):
    return sum(ord(c) * (i + 7) for i, c in enumerate(str(key)))


def tint_for(key):
    return get_color_from_hex(AV_COLORS[_hash(key) % len(AV_COLORS)])


def hex_for(key):
    return NAME_COLORS[_hash(key) % len(NAME_COLORS)].lstrip("#")


def letter_of(name):
    for ch in name or "?":
        if ch.isalnum():
            return ch.upper()
    return "?"


def initials(name):
    parts = [p for p in (name or "?").split() if p]
    if len(parts) >= 2:
        return letter_of(parts[0]) + letter_of(parts[1])
    return letter_of(name) + (parts[0][1].lower() if parts and len(parts[0]) > 1 else "")


def parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def local_dt(s):
    dt = parse_ts(s)
    return dt.astimezone() if dt else None


def fmt_ts(s):
    dt = local_dt(s)
    if not dt:
        return ""
    days = (datetime.datetime.now().astimezone().date() - dt.date()).days
    if days == 0:
        return "Today at " + dt.strftime("%H:%M")
    if days == 1:
        return "Yesterday at " + dt.strftime("%H:%M")
    return dt.strftime("%d.%m.%Y")


def day_key(m):
    dt = local_dt((m or {}).get("timestamp", ""))
    return dt.date() if dt else None


def fmt_day(m):
    dt = local_dt(m.get("timestamp", ""))
    return dt.strftime("%B %d, %Y").replace(" 0", " ") if dt else ""


# ---- widgets ----------------------------------------------------------- #
class _ImgHolder(FloatLayout):
    """FloatLayout that shows a remote image once it has loaded (until then the
    widgets underneath - tinted disc and letter - stay visible)."""
    src = StringProperty("")
    _img = None

    def on_src(self, *_):
        Clock.schedule_once(self._set_img, 0)

    def _set_img(self, *_):
        if self._img is not None:
            self.remove_widget(self._img)
            self._img = None
        if not self.src:
            return
        img = AsyncImage(source=self.src, fit_mode="cover", opacity=0)
        img.bind(on_load=lambda w, *a: setattr(w, "opacity", 1))
        self._img = img
        self.add_widget(img)


class Avatar(_ImgHolder):
    letter = StringProperty("?")
    tint = ListProperty([0.3, 0.3, 0.4, 1])


class IconTile(_ImgHolder):
    corner = NumericProperty(dp(24))


class RailItem(ButtonBehavior, FloatLayout):
    text = StringProperty("")
    icon = StringProperty("")
    src = StringProperty("")
    active = BooleanProperty(False)
    unread = BooleanProperty(False)
    tint = ListProperty([0.2, 0.2, 0.3, 1])
    _tile = None

    def _corner(self):
        return dp(16) if self.active else dp(24)

    def on_src(self, *_):
        Clock.schedule_once(self._set_tile, 0)

    def _set_tile(self, *_):
        if self._tile is not None:
            self.remove_widget(self._tile)
            self._tile = None
        if self.src:
            self._tile = IconTile(src=self.src, corner=self._corner(),
                                  pos_hint={"center_x": .5, "center_y": .5})
            self.add_widget(self._tile)

    def on_active(self, *_):
        if self._tile is not None:
            self._tile.corner = self._corner()


class ChannelRow(ButtonBehavior, BoxLayout):
    icon = StringProperty("pound")
    text = StringProperty("")
    sub = StringProperty("")
    active = BooleanProperty(False)
    unread = BooleanProperty(False)


class CategoryRow(ButtonBehavior, BoxLayout):
    text = StringProperty("")
    collapsed = BooleanProperty(False)


class TopStrip(BoxLayout):
    text = StringProperty("")
    busy = BooleanProperty(False)


class JumpPill(ButtonBehavior, BoxLayout):
    pass


class VoiceRow(BoxLayout):
    name = StringProperty("")
    letter = StringProperty("?")
    src = StringProperty("")
    tint = ListProperty([0.3, 0.3, 0.4, 1])
    muted = BooleanProperty(False)
    deaf = BooleanProperty(False)


class VoiceTile(VoiceRow):
    live = BooleanProperty(False)
    video = BooleanProperty(False)
    srv = BooleanProperty(False)


class AccountRow(ButtonBehavior, BoxLayout):
    name = StringProperty("")
    sub = StringProperty("")
    letter = StringProperty("?")
    src = StringProperty("")
    tint = ListProperty([0.3, 0.3, 0.4, 1])
    on_remove = ObjectProperty(lambda: None)


class MsgRow(BoxLayout):
    first = BooleanProperty(True)
    day = StringProperty("")
    state = StringProperty("")          # "", "sending", "failed"
    header = StringProperty("")
    body = StringProperty("")
    reply = StringProperty("")
    img = StringProperty("")
    letter = StringProperty("?")
    src = StringProperty("")
    tint = ListProperty([0.3, 0.3, 0.4, 1])
    msg = ObjectProperty(None, allownone=True)
    on_long = ObjectProperty(None, allownone=True)
    _ev = None

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._y0 = touch.y
            self._ev = Clock.schedule_once(self._fire, 0.45)
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._ev and abs(touch.y - self._y0) > dp(8):
            self._cancel()
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        self._cancel()
        return super().on_touch_up(touch)

    def _cancel(self):
        if self._ev:
            self._ev.cancel()
            self._ev = None

    def _fire(self, dt):
        self._ev = None
        if self.on_long:
            self.on_long(self.msg)


# ---- app ---------------------------------------------------------------- #
class BotcordApp(MDApp):
    gw_state = StringProperty("offline")
    gw_text = StringProperty("Offline")
    typing_text = StringProperty("")
    bar_text = StringProperty("")
    bar_icon = StringProperty("")
    notice = StringProperty("")
    # loading / chat status
    loading_msgs = BooleanProperty(False)
    chat_status = StringProperty("")
    chat_error = BooleanProperty(False)
    show_jump = BooleanProperty(False)
    loading_guilds = BooleanProperty(False)
    # the logged-in bot (user card in the channel panel)
    me_name = StringProperty("")
    me_src = StringProperty("")
    me_letter = StringProperty("?")
    me_tint = ListProperty([0.3, 0.3, 0.4, 1])

    def build(self):
        self.title = "Botcord"
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Indigo"
        Window.clearcolor = get_color_from_hex("#1E1F22")
        # "resize" keeps the top bar and the input visible when the keyboard opens
        # (the old "below_target" panned the whole window and pushed icons off screen)
        Window.softinput_mode = "resize"
        Window.bind(on_keyboard=self._on_key)
        self._reset_state()
        return Builder.load_string(KV)

    def _reset_state(self):
        self.token = None
        self.me_id = None
        self.gw = None
        self.guilds = {}
        self.users = {}
        self.pending_users = set()
        self.dms = {}
        self.unread = {}          # channel_id -> guild_id (None for DMs)
        self.collapsed = set()    # collapsed category ids
        self.view = "dm"          # "dm" or guild id
        self.cur_ch = None
        self.cur_voice = None
        # chat state
        self.msgs = []
        self.rows = {}
        self.pending = {}         # nonce -> optimistic local message
        self.typing = {}
        self.reply_to = None
        self.editing = None
        self.strip = None
        self.has_older = False
        self.loading_older = False
        self._buffer = []         # gateway messages that arrive while the first page loads
        self._armed = False       # upward paging only after the first render settled
        self._stick = True        # view is at the bottom -> follow new messages
        self._anchor_dist = 0
        self._chan_token = 0
        self._nonce_n = 0
        # list state
        self.rail_items = {}
        self.chan_rows = {}
        self._rail_ev = None
        self._panel_ev = None
        self._rail_token = 0
        self._panel_token = 0
        self._panel_built = None
        self._last_typing = 0
        self._dialog = None

    def on_start(self):
        Clock.schedule_interval(self._tick, 1.5)
        if am.list_accounts():
            self.show_accounts()
        else:
            self.goto("login")

    # ---- navigation ---------------------------------------------------- #
    def goto(self, name):
        self.root.current = name

    def back(self):
        cur = self.root.current
        if cur in ("chat", "voice"):
            self.cur_voice = None
            self.show_jump = False
            self.goto("home")
        elif cur == "home":
            self.show_accounts()
        elif cur == "login" and am.list_accounts():
            self.show_accounts()
        else:
            return False
        return True

    def _on_key(self, window, key, *args):
        if key == 27:
            return self.back()
        return False

    # ---- helpers ------------------------------------------------------- #
    def err(self, msg):
        Clock.schedule_once(lambda dt: toast(str(msg)[:120]))

    def bg(self, fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    def _feed(self, items, add, alive, done=None, n=14):
        """Add widgets in small chunks across frames so big lists never freeze the UI.
        The first chunk runs immediately; `alive()` False cancels the rest."""
        items = list(items)

        def step(dt=None):
            if not alive():
                return
            for _ in range(n):
                if not items:
                    break
                add(items.pop(0))
            if items:
                Clock.schedule_once(step, 0)
            elif done:
                done()
        step()

    def cache_user(self, u, member=None):
        if not u or "id" not in u:
            return
        name = (member or {}).get("nick") or u.get("global_name") or u.get("username") or "unknown"
        self.users[u["id"]] = {"name": name, "src": api.avatar_url(u) or "", "bot": bool(u.get("bot"))}

    def uinfo(self, uid):
        u = self.users.get(uid)
        return u or {"name": "user " + str(uid)[-4:], "src": "", "bot": False}

    def ensure_users(self, uids):
        for uid in uids:
            if uid in self.users or uid in self.pending_users:
                continue
            self.pending_users.add(uid)
            self.bg(self._fetch_user, uid)

    def _fetch_user(self, uid):
        try:
            u = api.get_user(self.token, uid)
            Clock.schedule_once(lambda dt: (self.cache_user(u), self._refresh_voice_views()))
        except Exception:
            pass

    def _ingest(self, m):
        """Remember every user a message mentions so names render properly."""
        self.cache_user(m.get("author"), m.get("member"))
        for u in m.get("mentions", []) or []:
            self.cache_user(u, u.get("member"))
        ref = m.get("referenced_message")
        if ref:
            self.cache_user(ref.get("author"))

    def _chan_name(self, cid):
        for g in self.guilds.values():
            c = g["channels"].get(cid)
            if c:
                return c.get("name", "channel")
        return "channel"

    def _clean(self, text):
        text = _RE_USER.sub(lambda mo: "@" + self.uinfo(mo.group(1))["name"], text)
        text = _RE_ROLE.sub("@role", text)
        text = _RE_CHAN.sub(lambda mo: "#" + self._chan_name(mo.group(1)), text)
        return _RE_EMOJI.sub(lambda mo: ":" + mo.group(1) + ":", text)

    # ---- login / accounts ---------------------------------------------- #
    def try_login(self, token):
        token = (token or "").strip()
        if not token:
            toast("Paste a bot token first")
            return
        toast("Logging in...")
        self.bg(self._do_login, token)

    def _do_login(self, token):
        try:
            me = api.get_me(token)
        except Exception as e:
            self.err(f"Login failed: {e}")
            return
        avatar_path = None
        url = api.get_avatar_url(me)
        if url:
            try:
                avatar_path = os.path.join(am.AVATAR_DIR, f"{me['id']}.png")
                with open(avatar_path, "wb") as f:
                    f.write(requests.get(url, timeout=15).content)
            except Exception:
                avatar_path = None
        display = me.get("global_name") or me.get("username")
        am.upsert_account(me["id"], token, me.get("username"), display, avatar_path)
        Clock.schedule_once(lambda dt: self.start_session(token, me))

    def show_accounts(self):
        self._stop_gateway()
        screen = self.root.get_screen("accounts")
        box = screen.ids.accounts_list
        box.clear_widgets()
        for acc in am.list_accounts():
            name = acc.get("display_name") or acc.get("username") or "bot"
            path = acc.get("avatar_path")
            row = AccountRow(
                name=name, sub="@" + str(acc.get("username")), letter=letter_of(name),
                src=path if path and os.path.exists(path) else "", tint=tint_for(acc["id"]),
                on_remove=lambda a=acc: self.confirm_remove(a["id"]),
            )
            row.bind(on_release=lambda w, a=acc: self.open_account(a["id"]))
            box.add_widget(row)
        self.goto("accounts")

    def open_account(self, account_id):
        try:
            token = am.get_token(account_id)
        except KeyError:
            return
        toast("Connecting...")
        self.bg(self._open_account_bg, token)

    def _open_account_bg(self, token):
        try:
            me = api.get_me(token)
        except Exception as e:
            self.err(f"Token rejected: {e}")
            return
        Clock.schedule_once(lambda dt: self.start_session(token, me))

    def confirm_remove(self, account_id):
        def do_remove(*_):
            am.remove_account(account_id)
            dialog.dismiss()
            self.show_accounts()
        dialog = MDDialog(
            title="Remove this login?",
            text="This deletes the saved token and cached avatar from this device.",
            buttons=[MDFlatButton(text="CANCEL", on_release=lambda x: dialog.dismiss()),
                     MDFlatButton(text="REMOVE", on_release=do_remove)],
        )
        dialog.open()

    # ---- session / gateway --------------------------------------------- #
    def _stop_gateway(self):
        if self.gw:
            self.gw.stop()
            self.gw = None
        for ev in (self._rail_ev, self._panel_ev):
            if ev:
                ev.cancel()
        self._rail_ev = self._panel_ev = None
        self.gw_state, self.gw_text = "offline", "Offline"

    def start_session(self, token, me):
        self._stop_gateway()
        self._reset_state()
        self.token = token
        self.me_id = me["id"]
        self.cache_user(me)
        self.me_name = me.get("global_name") or me.get("username") or "bot"
        self.me_src = api.avatar_url(me, 64) or ""
        self.me_letter = letter_of(self.me_name)
        self.me_tint = tint_for(me["id"])
        self.notice = ""
        self.loading_msgs = False
        self.chat_status = ""
        self.chat_error = False
        self.show_jump = False
        self.loading_guilds = True
        self.view = "dm"
        self.gw = Gateway(
            token,
            on_event=lambda t, d: Clock.schedule_once(lambda dt: self.on_gateway(t, d)),
            on_status=lambda s, txt: Clock.schedule_once(lambda dt: self.on_gw_status(s, txt)),
        )
        self.gw.start()
        self.bg(self._load_guilds_bg, token)
        self.rebuild_rail(now=True)
        self.rebuild_panel(now=True)
        self.goto("home")

    def on_gw_status(self, state, text):
        if state == "nocontent":
            self.notice = "Message Content intent is off - enable it in the Developer Portal to read server messages."
            return
        self.gw_state, self.gw_text = state, text
        if state == "error":
            toast(text)

    def _load_guilds_bg(self, tok):
        """Servers arrive page by page; each page shows up as soon as it is fetched."""
        after = None
        try:
            while True:
                page = api.get_guilds(tok, 100, after)
                Clock.schedule_once(lambda dt, p=page: self._merge_rest_guilds(tok, p))
                if len(page) < 100:
                    break
                after = page[-1]["id"]
        except Exception as e:
            self.err(f"Servers failed: {e}")
        Clock.schedule_once(lambda dt: self._guilds_done(tok))

    def _merge_rest_guilds(self, tok, page):
        if tok != self.token:
            return
        for g in page:
            st = self._gstore(g["id"])
            if st["name"] == "server":
                st["name"] = g.get("name", "server")
            st["icon"] = st.get("icon") or g.get("icon")
        self.rebuild_rail()

    def _guilds_done(self, tok):
        if tok != self.token:
            return
        self.loading_guilds = False
        self.rebuild_rail()
        if self.view != "dm":
            self.rebuild_panel()

    def on_gateway(self, t, d):
        h = getattr(self, "_ev_" + str(t).lower(), None)
        if h:
            try:
                h(d)
            except Exception as e:
                print("event error", t, e)

    def _gstore(self, gid):
        return self.guilds.setdefault(gid, {"id": gid, "name": "server", "icon": None,
                                            "channels": {}, "voice": {}})

    def _ev_guild_create(self, d):
        g = self._gstore(d["id"])
        g["name"] = d.get("name", g["name"])
        g["icon"] = d.get("icon") or g.get("icon")
        g["channels"] = {}
        for c in d.get("channels", []):
            c["guild_id"] = d["id"]
            g["channels"][c["id"]] = c
        g["voice"] = {vs["user_id"]: vs for vs in d.get("voice_states", [])}
        for m in d.get("members", []):
            self.cache_user(m.get("user"), m)
        self.ensure_users(list(g["voice"].keys()))
        self.rebuild_rail()
        if self.view == d["id"]:
            self.rebuild_panel()

    def _ev_guild_delete(self, d):
        if not d.get("unavailable"):
            self.guilds.pop(d["id"], None)
            if self.view == d["id"]:
                self.select_view("dm")
            self.rebuild_rail()

    def _ev_channel_create(self, c):
        gid = c.get("guild_id")
        if gid:
            self._gstore(gid)["channels"][c["id"]] = c
            if self.view == gid:
                self.rebuild_panel()

    _ev_channel_update = _ev_channel_create

    def _ev_channel_delete(self, c):
        gid = c.get("guild_id")
        if gid and gid in self.guilds:
            self.guilds[gid]["channels"].pop(c["id"], None)
            if self.view == gid:
                self.rebuild_panel()

    def _ev_voice_state_update(self, d):
        gid = d.get("guild_id")
        if not gid:
            return
        g = self._gstore(gid)
        uid = d["user_id"]
        if d.get("member"):
            self.cache_user(d["member"].get("user"), d["member"])
        if d.get("channel_id"):
            g["voice"][uid] = d
            self.ensure_users([uid])
        else:
            g["voice"].pop(uid, None)
        self._refresh_voice_views()

    def _ev_message_create(self, m):
        a = m.get("author") or {}
        self._ingest(m)
        cid, gid = m["channel_id"], m.get("guild_id")
        if not gid:
            self._register_dm(m)
        nonce = m.get("nonce")
        if nonce and nonce in self.pending:       # echo of a message we sent
            self._finalize(nonce, m)
            return
        if self.cur_ch and self.cur_ch["id"] == cid and self.root.current == "chat":
            self.typing.pop(a.get("id"), None)
            if self.loading_msgs:
                self._buffer.append(m)
            else:
                self._append_msg(m)
        elif a.get("id") != self.me_id:
            self._mark_unread(cid, gid)

    def _ev_message_update(self, m):
        if not (self.cur_ch and self.cur_ch["id"] == m.get("channel_id")):
            return
        for old in self.msgs:
            if old["id"] == m.get("id"):
                old.update({k: v for k, v in m.items() if v is not None})
                row = self.rows.get(old["id"])
                if row:
                    row.body = self._body(old)
                    row.img = self._img_of(old)
                break

    def _drop_row(self, mid):
        row = self.rows.pop(mid, None)
        if row and row.parent:
            row.parent.remove_widget(row)
        self.msgs = [x for x in self.msgs if x["id"] != mid]

    def _ev_message_delete(self, d):
        self._drop_row(d["id"])

    def _ev_message_delete_bulk(self, d):
        for mid in d.get("ids", []):
            self._drop_row(mid)

    def _ev_typing_start(self, d):
        if self.cur_ch and d.get("channel_id") == self.cur_ch["id"] and d.get("user_id") != self.me_id:
            if d.get("member"):
                self.cache_user(d["member"].get("user"), d["member"])
            self.ensure_users([d["user_id"]])
            self.typing[d["user_id"]] = time.time() + 9

    def _tick(self, dt):
        now = time.time()
        self.typing = {u: t for u, t in self.typing.items() if t > now}
        names = [self.uinfo(u)["name"] for u in self.typing]
        if not names:
            self.typing_text = ""
        elif len(names) == 1:
            self.typing_text = f"{names[0]} is typing..."
        elif len(names) == 2:
            self.typing_text = f"{names[0]} and {names[1]} are typing..."
        else:
            self.typing_text = "Several people are typing..."

    # ---- unread dots (updated in place - no list rebuilds) ---------------- #
    def _mark_unread(self, cid, gid):
        self.unread[cid] = gid
        row = self.chan_rows.get(cid)
        if row:
            row.unread = True
        self._refresh_unread_dots()

    def _refresh_unread_dots(self):
        vals = list(self.unread.values())
        for key, item in self.rail_items.items():
            item.unread = (None in vals) if key == "dm" else (key in vals)

    def _set_active(self, cid):
        for c, r in self.chan_rows.items():
            r.active = (c == cid)

    # ---- rail ------------------------------------------------------------- #
    def _home(self):
        return self.root.get_screen("home")

    def select_view(self, view):
        self.view = view
        for key, item in self.rail_items.items():
            item.active = (key == view)
        self.rebuild_panel(now=True)
        g = self.guilds.get(view)
        if view != "dm" and g and not g["channels"] and not g.get("loading"):
            g["loading"] = True
            g["failed"] = False
            self.bg(self._load_channels_bg, view)

    def _load_channels_bg(self, gid):
        try:
            chans = api.get_guild_channels(self.token, gid)
        except Exception as e:
            self.err(f"Channels failed: {e}")

            def failed(dt):
                g = self._gstore(gid)
                g["loading"], g["failed"] = False, True
                if self.view == gid:
                    self.rebuild_panel()
            Clock.schedule_once(failed)
            return

        def apply(dt):
            g = self._gstore(gid)
            g["loading"] = False
            for c in chans:
                c["guild_id"] = gid
                g["channels"][c["id"]] = c
            if self.view == gid:
                self.rebuild_panel()
        Clock.schedule_once(apply)

    def rebuild_rail(self, now=False):
        if not now:                       # coalesce bursts (e.g. many GUILD_CREATEs)
            if self._rail_ev is None:
                self._rail_ev = Clock.schedule_once(lambda dt: self.rebuild_rail(now=True), 0.15)
            return
        if self._rail_ev:
            self._rail_ev.cancel()
            self._rail_ev = None
        self._rail_token += 1
        token = self._rail_token
        box = self._home().ids.rail_box
        box.clear_widgets()
        self.rail_items = {}
        home = RailItem(icon="chat-processing", tint=get_color_from_hex("#383A40"),
                        active=self.view == "dm", unread=any(v is None for v in self.unread.values()))
        home.bind(on_release=lambda w: self.select_view("dm"))
        self.rail_items["dm"] = home
        box.add_widget(home)
        box.add_widget(_make_sep())
        items = []
        if self.loading_guilds and not self.guilds:      # skeleton while the first page loads
            items = [("skeleton", None)] * 4
        else:
            for gid, g in sorted(self.guilds.items(), key=lambda kv: kv[1]["name"].lower()):
                items.append(("guild", gid))

        def add(it):
            kind, gid = it
            if kind == "skeleton":
                box.add_widget(RailItem(tint=get_color_from_hex("#2B2D31")))
                return
            g = self.guilds.get(gid)
            if not g:
                return
            ri = RailItem(text=initials(g["name"]), tint=tint_for(gid), active=self.view == gid,
                          unread=gid in self.unread.values(), src=api.icon_url(gid, g.get("icon")) or "")
            ri.bind(on_release=lambda w, i=gid: self.select_view(i))
            self.rail_items[gid] = ri
            box.add_widget(ri)
        self._feed(items, add, lambda: token == self._rail_token, n=16)

    # ---- channel panel ------------------------------------------------------ #
    def rebuild_panel(self, now=False):
        if not now:
            if self._panel_ev is None:
                self._panel_ev = Clock.schedule_once(lambda dt: self.rebuild_panel(now=True), 0.12)
            return
        if self._panel_ev:
            self._panel_ev.cancel()
            self._panel_ev = None
        self._panel_token += 1
        token = self._panel_token
        fresh = self._panel_built != self.view       # switching view -> chunked, from the top
        self._panel_built = self.view
        scr = self._home()
        box = scr.ids.panel_box
        sv = box.parent
        off = (1 - sv.scroll_y) * max(0, box.height - sv.height)
        box.clear_widgets()
        self.chan_rows = {}
        items = []
        if self.view == "dm":
            scr.ids.panel_title.text = "Direct messages"
            items.append(self._new_dm_row)
            for cid, ch in self.dms.items():
                items.append(lambda cid=cid, ch=ch: self._dm_row(cid, ch))
            if not self.dms:
                items.append(lambda: TopStrip(text="No conversations yet", busy=False))
        else:
            g = self.guilds.get(self.view)
            if not g:
                scr.ids.panel_title.text = "Servers"
                if self.loading_guilds:
                    items.append(lambda: TopStrip(text="Loading servers...", busy=True))
            else:
                scr.ids.panel_title.text = g["name"]
                if not g["channels"]:
                    if g.get("failed"):
                        items.append(lambda: TopStrip(text="Couldn't load channels", busy=False))
                    else:
                        items.append(lambda: TopStrip(text="Loading channels...", busy=True))
                else:
                    self._panel_items(g, items)

        def restore():
            if fresh:
                sv.scroll_y = 1
                return

            def fix(dt):
                span = box.height - sv.height
                sv.scroll_y = 1 - min(1, off / span) if span > 0 else 1
            Clock.schedule_once(fix, 0)
            Clock.schedule_once(fix, 0.1)
        self._feed(items, lambda make: box.add_widget(make()),
                   lambda: token == self._panel_token, done=restore,
                   n=14 if fresh else 10_000)

    def _panel_items(self, g, items):
        chans = sorted(g["channels"].values(), key=lambda c: (c.get("position", 0), int(c["id"])))
        cats = [c for c in chans if c["type"] == 4]
        groups = [(None, [c for c in chans if c["type"] != 4 and not c.get("parent_id")])]
        for cat in cats:
            groups.append((cat, [c for c in chans if c["type"] != 4 and c.get("parent_id") == cat["id"]]))
        for cat, group in groups:
            group = [c for c in group if c["type"] in (0, 2, 5, 13)]
            if not group:
                continue
            closed = bool(cat) and cat["id"] in self.collapsed
            if cat:
                items.append(lambda cat=cat, closed=closed: self._cat_row(cat, closed))
            for c in group:
                cid = c["id"]
                if closed and cid not in self.unread and not (self.cur_ch and self.cur_ch["id"] == cid):
                    continue          # collapsed categories still show unread / open channels
                items.append(lambda c=c: self._channel_row(c, g))
                if c["type"] in (2, 13):
                    for uid, vs in g["voice"].items():
                        if vs.get("channel_id") == cid:
                            items.append(lambda uid=uid, vs=vs: self._voice_row(uid, vs))

    def _voice_row(self, uid, vs):
        u = self.uinfo(uid)
        return VoiceRow(name=u["name"], letter=letter_of(u["name"]), src=u["src"], tint=tint_for(uid),
                        muted=bool(vs.get("self_mute") or vs.get("mute")),
                        deaf=bool(vs.get("self_deaf") or vs.get("deaf")))

    def _cat_row(self, cat, closed):
        r = CategoryRow(text=cat["name"].upper(), collapsed=closed)
        r.bind(on_release=lambda w, cid=cat["id"]: self._toggle_cat(cid))
        return r

    def _toggle_cat(self, cid):
        if cid in self.collapsed:
            self.collapsed.discard(cid)
        else:
            self.collapsed.add(cid)
        self.rebuild_panel(now=True)

    def _channel_row(self, c, g):
        icon = {0: "pound", 5: "bullhorn", 2: "volume-high", 13: "access-point"}.get(c["type"], "pound")
        n = sum(1 for vs in g["voice"].values() if vs.get("channel_id") == c["id"]) if c["type"] in (2, 13) else 0
        r = ChannelRow(icon=icon, text=c.get("name", "channel"), sub=str(n) if n else "",
                       unread=c["id"] in self.unread,
                       active=bool(self.cur_ch and self.cur_ch["id"] == c["id"]))
        r.bind(on_release=lambda w, ch=c: self.open_channel(ch))
        self.chan_rows[c["id"]] = r
        return r

    def _new_dm_row(self):
        new = ChannelRow(icon="plus", text="New message by user ID")
        new.bind(on_release=lambda w: self.new_dm_dialog())
        return new

    def _dm_row(self, cid, ch):
        r = ChannelRow(icon="account", text=ch["name"], unread=cid in self.unread,
                       active=bool(self.cur_ch and self.cur_ch["id"] == cid))
        r.bind(on_release=lambda w, c=ch: self.open_channel(c))
        self.chan_rows[cid] = r
        return r

    # ---- DMs ------------------------------------------------------------ #
    def _register_dm(self, m):
        cid = m["channel_id"]
        a = m.get("author") or {}
        name = self.uinfo(a.get("id"))["name"] if a.get("id") != self.me_id else None
        if cid not in self.dms:
            self.dms[cid] = {"id": cid, "type": 1, "guild_id": None, "name": name or "DM " + cid[-4:]}
        elif name and self.dms[cid]["name"].startswith("DM "):
            self.dms[cid]["name"] = name
        if self.view == "dm":
            self.rebuild_panel()

    def new_dm_dialog(self):
        field = MDTextField(hint_text="User ID (shares a server with the bot)", mode="rectangle")
        content = MDBoxLayout(orientation="vertical", size_hint_y=None, height=dp(70), padding=(0, dp(8)))
        content.add_widget(field)

        def go(*_):
            uid = field.text.strip()
            dlg.dismiss()
            if uid:
                self.bg(self._open_dm_bg, uid)
        dlg = MDDialog(title="New DM", type="custom", content_cls=content,
                       buttons=[MDFlatButton(text="CANCEL", on_release=lambda x: dlg.dismiss()),
                                MDFlatButton(text="OPEN", on_release=go)])
        dlg.open()

    def _open_dm_bg(self, uid):
        try:
            ch = api.open_dm(self.token, uid)
            u = ch.get("recipients", [{}])[0]
        except Exception as e:
            self.err(f"Can't open DM: {e}")
            return

        def done(dt):
            self.cache_user(u)
            name = self.uinfo(u.get("id"))["name"]
            self.dms[ch["id"]] = {"id": ch["id"], "type": 1, "guild_id": None, "name": name}
            if self.view == "dm":
                self.rebuild_panel(now=True)
            self.open_channel(self.dms[ch["id"]])
        Clock.schedule_once(done)

    # ---- opening channels ----------------------------------------------- #
    def _chat(self):
        return self.root.get_screen("chat")

    def open_channel(self, ch):
        self.unread.pop(ch["id"], None)
        row = self.chan_rows.get(ch["id"])
        if row:
            row.unread = False
        self._refresh_unread_dots()
        if ch.get("type") in (2, 13):
            self.cur_voice = ch
            scr = self.root.get_screen("voice")
            scr.ids.voice_title.text = ch.get("name", "voice")
            scr.ids.voice_icon.icon = "volume-high" if ch["type"] == 2 else "access-point"
            self.refresh_voice()
            self.goto("voice")
            return
        self._chan_token += 1
        self.cur_ch = ch
        self._set_active(ch["id"])
        self.clear_bar()
        scr = self._chat()
        is_dm = ch.get("type") == 1
        scr.ids.chat_title.text = ch.get("name", "chat")
        scr.ids.chat_sub.text = "Direct message" if is_dm else self.guilds.get(ch.get("guild_id"), {}).get("name", "")
        scr.ids.chat_icon.icon = "account" if is_dm else "pound"
        scr.ids.msg_input.hint_text = "Message " + ("@" if is_dm else "#") + ch.get("name", "")
        box = scr.ids.messages_box
        box.clear_widgets()
        self.strip = TopStrip()
        box.add_widget(self.strip)
        self.msgs, self.rows, self.typing, self._buffer = [], {}, {}, []
        self.has_older = self.loading_older = False
        self._armed = False
        self._stick = True
        self.show_jump = False
        self.chat_error = False
        self.loading_msgs = True
        self.chat_status = "Loading messages..."
        self.goto("chat")
        self.bg(self._load_msgs_bg, ch["id"], None, self._chan_token)

    def retry_load(self):
        if self.cur_ch:
            self.open_channel(self.cur_ch)

    def _load_msgs_bg(self, cid, before, token):
        try:
            msgs = api.get_messages(self.token, cid, PAGE, before)
        except Exception as e:
            Clock.schedule_once(lambda dt: self._page_failed(token, before, e))
            return
        Clock.schedule_once(lambda dt: self._got_page(token, msgs, before))

    def _page_failed(self, token, before, e):
        if token != self._chan_token:
            return
        self.err(f"Messages failed: {e}")
        if before is None:
            self.loading_msgs = False
            self.chat_error = True
            self.chat_status = "Couldn't load messages"
        else:
            self.loading_older = False
            self._set_strip()
            self._armed = False          # back off a little before retrying
            Clock.schedule_once(lambda dt: setattr(self, "_armed", True), 3)

    def _got_page(self, token, msgs, before):
        if token != self._chan_token:
            return
        for m in msgs:
            self._ingest(m)
        self.has_older = len(msgs) >= PAGE
        if before is None:
            self.msgs = list(msgs)
            self._render_first_page(token)
        else:
            self._render_older(token, msgs)

    # first page: rows are added in chunks while "Loading messages..." is shown
    def _render_first_page(self, token):
        box = self._chat().ids.messages_box
        prev = [None]

        def add(m):
            row = self._make_row(m, prev[0])
            self.rows[m["id"]] = row
            box.add_widget(row)
            prev[0] = m

        def done():
            self.loading_msgs = False
            self.chat_status = "" if self.msgs else "No messages here yet. Say hi!"
            for m in self._buffer:                # live messages that arrived meanwhile
                if m["id"] not in self.rows:
                    self._append_msg(m)
            self._buffer = []
            self._set_strip()
            self._to_bottom()
            Clock.schedule_once(self._arm, 0.7)
        self._feed(self.msgs, add, lambda: token == self._chan_token, done, n=10)

    def _arm(self, dt=None):
        self._armed = True
        self._maybe_fill()

    def _maybe_fill(self, dt=None):
        """If the loaded messages don't even fill the screen, pull the next page."""
        if not self.cur_ch or self.loading_msgs or self.loading_older or not self.has_older:
            return
        sv = self._chat().ids.scroll
        box = self._chat().ids.messages_box
        if box.height <= sv.height + dp(40):
            self.load_older()

    # older pages: loaded when you scroll near the top, inserted above, view stays put
    def load_older(self):
        if self.loading_older or not self.has_older or not self.msgs or self.loading_msgs:
            return
        first = self.msgs[0]["id"]
        if str(first).startswith("local-"):
            return
        self.loading_older = True
        self._set_strip()
        self.bg(self._load_msgs_bg, self.cur_ch["id"], first, self._chan_token)

    def _render_older(self, token, older):
        if not older:
            self.loading_older = False
            self._set_strip()
            return
        scr = self._chat()
        box = scr.ids.messages_box
        self._begin_anchor()
        old_first = self.msgs[0] if self.msgs else None
        self.msgs = list(older) + self.msgs
        if old_first is not None and old_first["id"] in self.rows:
            self._restyle(self.rows[old_first["id"]], old_first, older[-1])
        order = list(range(len(older) - 1, -1, -1))        # newest -> oldest, inserted below the strip

        def add(i):
            m = older[i]
            row = self._make_row(m, older[i - 1] if i > 0 else None)
            self.rows[m["id"]] = row
            box.add_widget(row, index=len(box.children) - 1)

        def done():
            self.loading_older = False
            self._set_strip()
            Clock.schedule_once(self._end_anchor, 0.5)
        self._feed(order, add, lambda: token == self._chan_token, done, n=10)

    def _begin_anchor(self):
        sv, box = self._chat().ids.scroll, self._chat().ids.messages_box
        self._anchor_dist = max(0, sv.scroll_y * (box.height - sv.height))
        box.unbind(height=self._on_anchor_height)
        box.bind(height=self._on_anchor_height)

    def _on_anchor_height(self, box, h):
        sv = self._chat().ids.scroll
        span = h - sv.height
        if span > 0:
            sv.scroll_y = min(1, max(0, self._anchor_dist / span))

    def _end_anchor(self, dt=None):
        self._chat().ids.messages_box.unbind(height=self._on_anchor_height)
        self._maybe_fill()

    def _set_strip(self):
        s = self.strip
        if s is None or not self.cur_ch:
            return
        if self.loading_older:
            s.text, s.busy = "Loading older messages...", True
        elif not self.has_older and self.msgs and not self.loading_msgs:
            name = self.cur_ch.get("name", "")
            s.text = ("Beginning of your chat with " + name) if self.cur_ch.get("type") == 1 else "Beginning of #" + name
            s.busy = False
        else:
            s.text, s.busy = "", False

    def scroll_moved(self, sv, y):
        if self.root is None:
            return
        box = self._chat().ids.messages_box
        span = box.height - sv.height
        if span <= 0:
            self.show_jump = False
            return
        dist = y * span                                    # px from the bottom
        self._stick = dist < dp(80)
        self.show_jump = dist > dp(600) and not self.loading_msgs
        if self._armed and self.has_older and not self.loading_older and (span - dist) < dp(500):
            self.load_older()

    def view_resized(self):
        if self.root is not None and self._stick:
            Clock.schedule_once(lambda dt: self._to_bottom(), 0.05)

    def jump_latest(self):
        self._to_bottom()
        self.show_jump = False

    def _to_bottom(self, *_):
        if self.root is None:
            return
        sv = self._chat().ids.scroll
        sv.scroll_y = 0
        Clock.schedule_once(lambda dt: setattr(sv, "scroll_y", 0), 0.1)

    # ---- message rows ------------------------------------------------------ #
    def _append_msg(self, m):
        if m["id"] in self.rows:
            return
        prev = self.msgs[-1] if self.msgs else None
        self.msgs.append(m)
        row = self._make_row(m, prev)
        self.rows[m["id"]] = row
        self._chat().ids.messages_box.add_widget(row)
        if self.chat_status and not self.loading_msgs:
            self.chat_status = ""
        self._set_strip()
        if self._stick or (m.get("author") or {}).get("id") == self.me_id:
            self._to_bottom()

    def _img_of(self, m):
        for a in m.get("attachments", []) or []:
            if str(a.get("content_type", "")).startswith("image/"):
                return a.get("url") or a.get("proxy_url") or ""
        return ""

    def _body(self, m):
        t = m.get("type", 0)
        if t not in (0, 19, 20, 23):
            txt = SYSTEM_TEXT.get(t, "(system message)")
        else:
            txt = self._clean(m.get("content") or "")
        img = self._img_of(m)
        for a in m.get("attachments", []) or []:
            if str(a.get("content_type", "")).startswith("image/"):
                continue
            txt += ("\n" if txt else "") + "[file] " + str(a.get("filename", "file"))
        if not txt and not img and m.get("embeds"):
            txt = "[embed] " + str(m["embeds"][0].get("title") or m["embeds"][0].get("description", ""))[:120]
        if not txt and not img:
            txt = "(no text - message content may be hidden)"
        if m.get("edited_timestamp"):
            txt += "  (edited)"
        return txt

    def _header(self, m):
        a = m.get("author") or {}
        uid = a.get("id", "0")
        u = self.uinfo(uid)
        bot = "  [color=5865f2][size=10sp][b]BOT[/b][/size][/color]" if a.get("bot") else ""
        return (f"[b][color={hex_for(uid)}]{escape_markup(u['name'])}[/color][/b]{bot}  "
                f"[size=11sp][color=949ba4]{fmt_ts(m.get('timestamp', ''))}[/color][/size]")

    def _flags(self, m, prev):
        """(first-of-group, day divider text) for message m after prev."""
        uid = (m.get("author") or {}).get("id")
        if prev is None or day_key(prev) != day_key(m):
            return True, fmt_day(m)
        first = True
        if (prev.get("author") or {}).get("id") == uid and not m.get("message_reference") \
                and m.get("type", 0) in (0, 19):
            t1, t0 = parse_ts(m.get("timestamp", "")), parse_ts(prev.get("timestamp", ""))
            if t1 and t0 and (t1 - t0).total_seconds() < 420:
                first = False
        return first, ""

    def _restyle(self, row, m, prev):
        row.first, row.day = self._flags(m, prev)

    def _make_row(self, m, prev):
        a = m.get("author") or {}
        uid = a.get("id", "0")
        u = self.uinfo(uid)
        first, day = self._flags(m, prev)
        reply = ""
        ref = m.get("referenced_message")
        if ref:
            ra = (ref.get("author") or {}).get("id")
            reply = "@" + self.uinfo(ra)["name"] + "  " + self._clean((ref.get("content") or "").replace("\n", " "))[:60]
        state = "failed" if m.get("_failed") else ("sending" if m.get("_pending") else "")
        return MsgRow(first=first, day=day, state=state, header=self._header(m), body=self._body(m),
                      reply=reply, img=self._img_of(m), letter=letter_of(u["name"]), src=u["src"],
                      tint=tint_for(uid), msg=m, on_long=self.message_menu)

    # ---- sending (optimistic) ---------------------------------------------- #
    def send_current(self):
        ti = self._chat().ids.msg_input
        text = ti.text.strip()
        if not text or not self.cur_ch:
            return
        cid = self.cur_ch["id"]
        edit, reply = self.editing, self.reply_to
        self.editing = None
        self.reply_to = None
        ti.text = ""
        self.bar_text = self.bar_icon = ""
        if edit:
            self._edit_now(cid, edit, text)
            return
        self._nonce_n += 1
        nonce = f"{int(time.time() * 1000)}{self._nonce_n % 100:02d}"
        local = {"id": "local-" + nonce, "channel_id": cid, "content": text,
                 "author": {"id": self.me_id, "bot": True}, "timestamp": now_iso(),
                 "nonce": nonce, "_pending": True, "_reply_id": reply["id"] if reply else None,
                 "referenced_message": reply, "message_reference": {"message_id": reply["id"]} if reply else None}
        self.pending[nonce] = local
        self._append_msg(local)                      # shows up instantly
        self.bg(self._send_bg, cid, text, local["_reply_id"], nonce)

    def _send_bg(self, cid, text, reply_id, nonce):
        try:
            real = api.send_message(self.token, cid, text, reply_id, nonce)
        except Exception as e:
            Clock.schedule_once(lambda dt: self._send_failed(nonce, e))
            return
        Clock.schedule_once(lambda dt: self._finalize(nonce, real))

    def _finalize(self, nonce, real):
        """Swap the optimistic row for the confirmed message (first of REST / gateway wins)."""
        local = self.pending.pop(nonce, None)
        if not local:
            return
        row = self.rows.pop(local["id"], None)
        if real["id"] in self.rows:                  # defensive: the real one is already shown
            self.msgs = [x for x in self.msgs if x is not local]
            if row is not None and row.parent:
                row.parent.remove_widget(row)
            return
        for i, x in enumerate(self.msgs):
            if x is local:
                self.msgs[i] = real
                break
        if row is None:
            return
        row.msg = real
        row.state = ""
        row.header = self._header(real)
        row.body = self._body(real)
        self.rows[real["id"]] = row

    def _send_failed(self, nonce, e):
        local = self.pending.get(nonce)
        if not local:
            return
        local["_failed"] = True
        row = self.rows.get(local["id"])
        if row:
            row.state = "failed"
        self.err(f"Send failed: {e}")

    def _retry_send(self, local):
        local["_failed"] = False
        row = self.rows.get(local["id"])
        if row:
            row.state = "sending"
        self.bg(self._send_bg, local["channel_id"], local["content"], local.get("_reply_id"), local["nonce"])

    def _discard_local(self, local):
        self.pending.pop(local.get("nonce"), None)
        self._drop_row(local["id"])

    def _edit_now(self, cid, m, text):
        if text == (m.get("content") or ""):
            return
        old = (m.get("content"), m.get("edited_timestamp"))
        m["content"], m["edited_timestamp"] = text, now_iso()
        row = self.rows.get(m["id"])
        if row:
            row.body = self._body(m)
        self.bg(self._edit_bg, cid, m, text, old)

    def _edit_bg(self, cid, m, text, old):
        try:
            api.edit_message(self.token, cid, m["id"], text)
        except Exception as e:
            def revert(dt):
                m["content"], m["edited_timestamp"] = old
                row = self.rows.get(m["id"])
                if row:
                    row.body = self._body(m)
                self.err(f"Edit failed: {e}")
            Clock.schedule_once(revert)

    def delete_msg(self, m):
        cid, mid = m["channel_id"], m["id"]
        self._drop_row(mid)                          # disappears instantly

        def run():
            try:
                api.delete_message(self.token, cid, mid)
            except Exception as e:
                self.err(f"Delete failed: {e}")
                if self.cur_ch and self.cur_ch["id"] == cid:   # put it back
                    Clock.schedule_once(lambda dt: self.retry_load())
        self.bg(run)

    # ---- message actions ------------------------------------------------- #
    def message_menu(self, m):
        if not m:
            return
        mine = (m.get("author") or {}).get("id") == self.me_id
        if m.get("_pending"):
            opts = [("Copy text", lambda: self.copy_msg(m))]
            if m.get("_failed"):
                opts = [("Retry", lambda: self._retry_send(m)), ("Discard", lambda: self._discard_local(m))] + opts
        else:
            opts = [("Reply", lambda: self.set_reply(m)), ("Copy text", lambda: self.copy_msg(m))]
            if mine:
                opts += [("Edit", lambda: self.set_edit(m)), ("Delete", lambda: self.delete_msg(m))]

        def wrap(fn):
            return lambda *_: (self._dialog.dismiss(), fn())
        self._dialog = MDDialog(title="Message", type="simple",
                                items=[OneLineListItem(text=t, on_release=wrap(f)) for t, f in opts])
        self._dialog.open()

    def copy_msg(self, m):
        Clipboard.copy(m.get("content", ""))
        toast("Copied")

    def set_reply(self, m):
        self.editing = None
        self.reply_to = m
        self.bar_icon = "reply"
        self.bar_text = "Replying to " + self.uinfo((m.get("author") or {}).get("id")).get("name", "?")
        self._chat().ids.msg_input.focus = True

    def set_edit(self, m):
        self.reply_to = None
        self.editing = m
        self.bar_icon = "pencil"
        self.bar_text = "Editing message"
        ti = self._chat().ids.msg_input
        ti.text = m.get("content", "")
        ti.focus = True

    def clear_bar(self):
        if self.editing:
            try:
                self._chat().ids.msg_input.text = ""
            except Exception:
                pass
        self.reply_to = self.editing = None
        self.bar_text = self.bar_icon = ""

    def on_typing(self, text):
        if not text or not self.cur_ch or self.editing:
            return
        if time.time() - self._last_typing > 8:
            self._last_typing = time.time()
            cid = self.cur_ch["id"]
            self.bg(lambda: api.trigger_typing(self.token, cid))

    # ---- voice presence --------------------------------------------------- #
    def _voice_members(self):
        ch = self.cur_voice
        if not ch:
            return []
        g = self.guilds.get(ch.get("guild_id"), {})
        return [(uid, vs) for uid, vs in g.get("voice", {}).items() if vs.get("channel_id") == ch["id"]]

    def _refresh_voice_views(self):
        if self.cur_voice and self.root.current == "voice":
            self.refresh_voice()
        if self.view != "dm":
            self.rebuild_panel()

    def refresh_voice(self):
        scr = self.root.get_screen("voice")
        box = scr.ids.voice_box
        box.clear_widgets()
        members = self._voice_members()
        scr.ids.voice_count.text = f"{len(members)} connected" if members else "Nobody here yet"
        for uid, vs in members:
            u = self.uinfo(uid)
            box.add_widget(VoiceTile(
                name=u["name"] + ("  (this bot)" if uid == self.me_id else ""), letter=letter_of(u["name"]),
                src=u["src"], tint=tint_for(uid),
                muted=bool(vs.get("self_mute") or vs.get("mute")),
                deaf=bool(vs.get("self_deaf") or vs.get("deaf")),
                srv=bool(vs.get("mute") or vs.get("deaf")),
                live=bool(vs.get("self_stream")), video=bool(vs.get("self_video"))))

    # ---- bot status -------------------------------------------------------- #
    def status_dialog(self):
        def pick(s):
            return lambda *_: (self._dialog.dismiss(), self.gw and self.gw.set_presence(s))
        items = [("Online", "online"), ("Idle", "idle"), ("Do not disturb", "dnd"), ("Invisible", "invisible")]
        self._dialog = MDDialog(title="Bot status", type="simple",
                                items=[OneLineListItem(text=t, on_release=pick(s)) for t, s in items])
        self._dialog.open()


def _make_sep():
    return Factory.RailSep()


if __name__ == "__main__":
    BotcordApp().run()
