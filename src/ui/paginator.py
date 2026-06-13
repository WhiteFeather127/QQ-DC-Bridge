from __future__ import annotations

import discord


class ToggleOriginalView(discord.ui.View):
    def __init__(self, original: str, translated: str, ephemeral: bool = False) -> None:
        super().__init__(timeout=300)
        self.original = original
        self.translated = translated
        self.ephemeral = ephemeral
        if ephemeral:
            self.toggle_btn.label = "Close"

    @discord.ui.button(label="View Original", style=discord.ButtonStyle.secondary)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.ephemeral:
            await interaction.response.defer()
            await interaction.delete_original_response()
        else:
            ephem_view = ToggleOriginalView(self.original, self.translated, ephemeral=True)
            await interaction.response.send_message(content=self.original, ephemeral=True, view=ephem_view)
