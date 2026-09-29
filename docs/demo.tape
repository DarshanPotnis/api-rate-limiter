# Records scripts/demo.py against a running stack into docs/demo.gif with VHS
# (https://github.com/charmbracelet/vhs). From the repository root:
#
#   docker compose down && docker compose up -d --wait   # fresh limits and breaker state
#   brew services stop ollama                            # optional: shows the fallback and the breaker
#   vhs docs/demo.tape
#
# The tape activates .venv, which needs the openai SDK (pip install -r requirements-dev.txt).

Output docs/demo.gif

Require python3

Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 820
Set Padding 24
Set TypingSpeed 45ms

Hide
Type "source .venv/bin/activate && clear"
Enter
Show

Type "python scripts/demo.py --pace 1.5"
Sleep 500ms
Enter

# Five steps with a 1.5 s pause before each, then a few seconds on the final screen.
Sleep 12s
