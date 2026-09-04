#!/usr/bin/env bash
#
# Spin up / tear down a local Postgres container for dataregistry development.
#
# Subcommands:
#   up [--no-seed]      Start container, create schemas, seed fake data
#                       (skipped with --no-seed), print env block.
#   down                Stop container (keeps the data volume).
#   reset [--no-seed]   Stop container AND delete the data volume, then `up`.
#   status              Show container status and pointer instructions.
#   env                 Print only the `export` lines (for `eval $(./dev-db.sh env)`).
#   psql                Open a psql shell inside the container.
#   seed                Re-run the seed script against the running container.
#
# After `up`, point the library at the dev DB with:
#   eval "$(./dev/dev-db.sh env)"
# or by exporting DATAREG_CONFIG and DATAREG_SITE manually (status will show how).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
CONFIG_FILE="$SCRIPT_DIR/dataregistry.dev.yaml"
ROOT_DIR_DEFAULT="$SCRIPT_DIR/root_dir"
ROOT_DIR="${DATAREG_DEV_ROOT_DIR:-$ROOT_DIR_DEFAULT}"
SEED_SCRIPT="$SCRIPT_DIR/seed_dev_data.py"
CREATE_SCHEMA_SCRIPT="$REPO_ROOT/scripts/create_registry_schema.py"

# Pick the compose CLI: prefer `docker compose`, fall back to `docker-compose`.
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose -f "$COMPOSE_FILE")
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose -f "$COMPOSE_FILE")
else
    echo "error: neither 'docker compose' nor 'docker-compose' is available" >&2
    exit 1
fi

usage() {
    sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

wait_for_healthy() {
    # The compose healthcheck handles the actual probing — we just poll its
    # state until it goes 'healthy', up to ~60s.
    local container="dataregistry-dev-postgres"
    local i=0
    while [ $i -lt 60 ]; do
        local state
        state="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")"
        if [ "$state" = "healthy" ]; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "error: postgres container did not become healthy in 60s" >&2
    docker logs --tail 50 "$container" >&2 || true
    return 1
}

print_env() {
    # DATAREG_SITE isn't useful here: site_rootdir.yaml is bundled with the
    # installed package and has no 'dev' entry, and the library offers no env
    # override for that path. Instead we export DATAREG_DEV_ROOT_DIR for the
    # user to plumb into DataRegistry(root_dir=...) by hand.
    cat <<EOF
export DATAREG_CONFIG="$CONFIG_FILE"
export DATAREG_DEV_ROOT_DIR="$ROOT_DIR"
# Pass root_dir=\$DATAREG_DEV_ROOT_DIR explicitly when constructing
# DataRegistry (no \`site\` will work). See dev/README.md for an example.
EOF
}

print_status() {
    echo "Compose file:  $COMPOSE_FILE"
    echo "Config file:   $CONFIG_FILE"
    echo "Root dir:      $ROOT_DIR"
    echo
    "${COMPOSE[@]}" ps
    echo
    echo "To point the library at this DB:"
    echo "  eval \"\$(./dev/dev-db.sh env)\""
    echo "Then construct DataRegistry(root_dir=\"$ROOT_DIR\", ...)."
}

cmd_up() {
    local seed=1
    for arg in "$@"; do
        case "$arg" in
            --no-seed) seed=0 ;;
            *) echo "error: unknown option '$arg' for 'up'" >&2; exit 2 ;;
        esac
    done

    # The library refuses to read a password-bearing config file that's group-
    # or world-readable (see db_basic.py). Tighten the perms before any tool
    # below tries to use it.
    chmod 600 "$CONFIG_FILE"

    echo ">> starting postgres container"
    "${COMPOSE[@]}" up -d
    echo ">> waiting for postgres to be healthy"
    wait_for_healthy

    mkdir -p "$ROOT_DIR"

    # The schema-creation script writes a provenance row, which fails if the
    # schemas already exist. Detect that case so reruns are idempotent.
    if docker exec dataregistry-dev-postgres \
            psql -U postgres -d desc_data_registry -tAc \
            "SELECT 1 FROM information_schema.schemata WHERE schema_name='lsst_desc_working'" \
            | grep -q 1; then
        echo ">> schemas already present, skipping create"
    else
        echo ">> creating dataregistry schemas (working + production)"
        python "$CREATE_SCHEMA_SCRIPT" \
            --config "$CONFIG_FILE" \
            --create_both \
            --no_permission_restrictions
    fi

    if [ "$seed" -eq 1 ]; then
        echo ">> seeding fake data"
        python "$SEED_SCRIPT" --root-dir "$ROOT_DIR"
    else
        echo ">> --no-seed: skipping seed step"
    fi

    echo
    echo "dev DB is ready."
    echo
    print_env
}

cmd_down() {
    echo ">> stopping postgres container (data volume preserved)"
    "${COMPOSE[@]}" down
}

cmd_reset() {
    echo ">> stopping postgres container and removing data volume"
    "${COMPOSE[@]}" down -v
    rm -rf "$ROOT_DIR"
    cmd_up "$@"
}

cmd_status() {
    print_status
}

cmd_env() {
    print_env
}

cmd_psql() {
    docker exec -it dataregistry-dev-postgres \
        psql -U postgres -d desc_data_registry "$@"
}

cmd_seed() {
    python "$SEED_SCRIPT" --root-dir "$ROOT_DIR" "$@"
}

main() {
    local sub="${1:-}"
    if [ -z "$sub" ] || [ "$sub" = "-h" ] || [ "$sub" = "--help" ]; then
        usage
        exit 0
    fi
    shift
    case "$sub" in
        up)     cmd_up "$@" ;;
        down)   cmd_down "$@" ;;
        reset)  cmd_reset "$@" ;;
        status) cmd_status "$@" ;;
        env)    cmd_env "$@" ;;
        psql)   cmd_psql "$@" ;;
        seed)   cmd_seed "$@" ;;
        *)
            echo "error: unknown subcommand '$sub'" >&2
            usage
            exit 2
            ;;
    esac
}

main "$@"
