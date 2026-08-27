from shared.ui import FrameUi

class TwoFactorUi(FrameUi):
    def usage(self) -> None:
        self.title("2FA")
        self.step("Usage: 2fa [command]")
        self.command("2fa", "Prompt for Base32 secret; copy 6-digit TOTP")
        self.command("2fa (help | -h | --help)", "Show this help")
        self.section("Notes")
        self.note(
            "Secret is read hidden from the terminal; code goes to the clipboard."
        )
        self.end()

    def begin_totp(self) -> None:
        self.title("2FA")
        self.step("Generate TOTP")

    def secret_prompt(self) -> str:
        return self.prompt_text("Base32 secret: ")

    def success_copied(self, code: str) -> None:
        self.line(self.pipe)
        self.step(f"Code copied: {code}")
        self.end()
