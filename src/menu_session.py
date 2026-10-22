"""Private menu lifecycle and small Discord component helpers."""

import time

import disnake

from bot_runtime import EPHEMERAL_DELETE_SECONDS
from i18n import t
from state import can_configure_channels


def page_slice(items, page, size=25):
    pages = max(1, (len(items) + size - 1) // size)
    page = min(max(0, page), pages - 1)
    return items[page * size:(page + 1) * size], page, pages


class MenuSession:
    def __init__(self, owner_id, admin=False):
        self.owner_id = int(owner_id)
        self.admin = admin
        self.deadline = time.monotonic() + EPHEMERAL_DELETE_SECONDS
        self.message = None
        self.view = None
        self.closed = False

    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    async def check(self, inter, data):
        valid = (
            not self.closed and self.remaining() > 0
            and inter.author.id == self.owner_id
            and (not self.admin or (inter.guild and can_configure_channels(inter)))
        )
        if not valid:
            await inter.response.send_message(t(data, "menus.unavailable"), ephemeral=True)
        return bool(valid)

    async def acknowledge(self, inter):
        if not inter.response.is_done():
            await inter.response.defer()

    async def show(self, inter, embed, view, content=None):
        if self.closed or self.remaining() <= 0:
            view.stop()
            await self.expire()
            return
        previous = self.view
        if self.message is None:
            if not inter.response.is_done():
                await inter.response.defer(ephemeral=True)
            self.message = await inter.followup.send(content=content, embed=embed, view=view, ephemeral=True, wait=True)
        else:
            await self.acknowledge(inter)
            await self.message.edit(content=content, embed=embed, view=view, attachments=[])
        self.view = view
        if previous is not None and previous is not view:
            previous.stop()

    async def expire(self):
        self.closed = True
        if not self.message:
            return
        try:
            await self.message.delete()
        except disnake.HTTPException:
            if self.view:
                for item in self.view.children:
                    item.disabled = True
                try:
                    await self.message.edit(view=self.view)
                except disnake.HTTPException:
                    pass
        if self.view:
            self.view.stop()


class SessionView(disnake.ui.View):
    def __init__(self, session, data):
        super().__init__(timeout=max(0.01, session.remaining()))
        self.session = session
        self.data = data

    async def interaction_check(self, inter):
        return await self.session.check(inter, self.data)

    async def on_timeout(self):
        await self.session.expire()

    async def on_error(self, error, item, inter):
        import logging
        logging.getLogger(__name__).error("Private menu callback failed", exc_info=(type(error), error, error.__traceback__))
        if not inter.response.is_done():
            await inter.response.defer()
        if self.session.message and not self.session.closed:
            try:
                await self.session.message.edit(content=t(self.data, "menus.busy"))
            except disnake.HTTPException:
                pass


class MenuButton(disnake.ui.Button):
    def __init__(self, label, callback, *, row=4, style=disnake.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label[:80], row=row, style=style, disabled=disabled)
        self.handler = callback

    async def callback(self, inter):
        await self.handler(inter)


class MenuSelect(disnake.ui.Select):
    def __init__(self, placeholder, options, callback, row=1):
        super().__init__(placeholder=placeholder[:150], options=options, row=row)
        self.handler = callback

    async def callback(self, inter):
        await self.handler(inter, self.values[0])


def add_pager(view, page, pages, callback, data, row=3):
    async def previous(inter):
        await callback(inter, page - 1)

    async def following(inter):
        await callback(inter, page + 1)

    view.add_item(MenuButton(t(data, "menus.previous"), previous, row=row, disabled=page == 0))
    view.add_item(MenuButton(f"{page + 1} / {pages}", previous, row=row, disabled=True))
    view.add_item(MenuButton(t(data, "menus.next"), following, row=row, disabled=page + 1 >= pages))
