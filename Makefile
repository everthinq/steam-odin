# ==============================================================================
# THE SAGA OF STEAM-ODIN
# ==============================================================================
# Use 'make help' (or just 'make') to hear the songs of the commands.

.PHONY: help odin forge raid bifrost sleep ragnarok helheim saga
.PHONY: all build up dev down clean prune logs asf-setup asf asf-password ratatoskr-secret

# Default: Speak the wisdom
help:
	@echo ""
	@echo "  \033[1;33m⚡ STEAM-ODIN COMMANDS ⚡\033[0m"
	@echo ""
	@echo "  \033[0;32modin\033[0m      (all)    The All-Father commands everything (Build + Raid)"
	@echo "  \033[0;32mforge\033[0m     (build)  Forge the containers in the fire of creation"
	@echo "  \033[0;32mraid\033[0m      (up)     Launch the longships in the background"
	@echo "  \033[0;32mbifrost\033[0m   (dev)    Open the bridge (Attached logs/interactive)"
	@echo "  \033[0;32msleep\033[0m     (down)   Rest the warriors"
	@echo "  \033[0;32msaga\033[0m      (logs)   Read the tales of the execution"
	@echo "  \033[0;32mragnarok\033[0m  (clean)  Destruction and renewal (Stop + remove orphans)"
	@echo "  \033[0;32mhelheim\033[0m   (prune)  Send unused spirits to the underworld"
	@echo "  \033[0;32masf-setup\033[0m          One-time ArchiSteamFarm setup (password + hardened config)"
	@echo "  \033[0;32masf\033[0m                Start ArchiSteamFarm and reconnect Heimdall to it"
	@echo "  \033[0;32masf-password\033[0m       Copy the ASF UI password to the clipboard (never printed)"
	@echo "  \033[0;32mratatoskr-secret\033[0m   One-time: the secret only the backend may call Ratatoskr with (root .env)"
	@echo ""

# ------------------------------------------------------------------------------
# THE COMMANDS OF POWER
# ------------------------------------------------------------------------------

# ODIN: The All-Father commands everything
all: odin
odin: forge raid

# FORGE: Forging the containers like mythical weapons
build: forge
forge:
	docker compose build

# RAID: Launching the fleet (Detached)
up: raid
raid:
	docker compose up -d

# BIFROST: The bridge to the realm (Attached/Interactive)
dev: bifrost
bifrost:
	docker compose up

# SLEEP: Putting the warriors to rest
down: sleep
sleep:
	docker compose down

# SAGA: Reading the tales
logs: saga
saga:
	docker compose logs -f

# RAGNAROK: Destruction and renewal
clean: ragnarok
ragnarok:
	docker compose down --remove-orphans
	docker image prune -f

# HELHEIM: Deep clean, sending unused assets to the underworld
# WARNING: This deletes all stopped containers and unused images
prune: helheim
helheim:
	docker system prune -f

# ------------------------------------------------------------------------------
# THE APPS (INDIVIDUAL REALMS)
# ------------------------------------------------------------------------------

# HEIMDALL: The Watchman (Authenticator)
heimdall:
	docker compose up -d heimdall-backend heimdall-frontend

# RATATOSKR: The Courier (Casemove)
ratatoskr:
	# Assuming future service name 'ratatoskr'
	@echo "Ratatoskr is still growing in the World Tree..."

# HUGINN: The Scout (Skinsprice)
huginn:
	# Assuming future service name 'huginn'
	@echo "Huginn is flying over Midgard..."

# ASF: ArchiSteamFarm, the card farmer Andvari drives (see Yggdrasil/asf/README.md)
asf-setup:
	python3 scripts/asf_setup.py

asf:
	docker compose up -d asf
	docker compose up -d heimdall-backend

asf-password:
	@grep '^ASF_IPC_PASSWORD=' .env | cut -d= -f2- | tr -d '\n' | pbcopy && echo "ASF UI password copied to the clipboard (open http://localhost:1242)"

# Ratatoskr refuses every request without this secret; the backend sends it. Written once
# into the root .env (gitignored, owner-only), never printed. Then: docker compose up -d
ratatoskr-secret:
	@touch .env && chmod 600 .env
	@if grep -q '^RATATOSKR_SECRET=.' .env; then echo "RATATOSKR_SECRET already set in .env"; \
	else echo "RATATOSKR_SECRET=$$(openssl rand -hex 32)" >> .env && echo "RATATOSKR_SECRET written to .env — run: docker compose up -d heimdall-backend ratatoskr"; fi
