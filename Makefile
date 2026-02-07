# ==============================================================================
# THE SAGA OF STEAM-ODIN
# ==============================================================================
# Use 'make help' (or just 'make') to hear the songs of the commands.

.PHONY: help odin forge raid bifrost sleep ragnarok helheim saga
.PHONY: all build up dev down clean prune logs

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
