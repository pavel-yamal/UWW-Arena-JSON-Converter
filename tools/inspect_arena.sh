#!/bin/sh
# Collects (read-only) what the vMix service needs from a running UWW Arena install:
# Mercure hub/topics/payloads, fight & timer fields, API auth setup.
# Nothing in Arena is changed. Result: arena_inspect.txt next to this script's cwd.
#
# Run from the folder with Arena's docker-compose.yaml:
#   sh inspect_arena.sh            (Linux/macOS/WSL)
# Container name differs? set it: CONTAINER=arena-php-fpm-1 sh inspect_arena.sh

C="${CONTAINER:-$(docker ps --format '{{.Names}}' | grep -m1 'php-fpm')}"
OUT="arena_inspect.txt"
[ -z "$C" ] && { echo "Arena php-fpm container not found (docker ps)"; exit 1; }
echo "container: $C -> $OUT"

docker exec -i "$C" sh -s 2>&1 <<'EOS' | sed -E 's/((SECRET|PASSWORD|_KEY|TOKEN)[A-Z_]*["\x27]?[[:space:]]*(=>|[:=])[[:space:]]*["\x27]?)[^"\x27[:space:],)]{6,}/\1***hidden***/I' > "$OUT"
R=/var/www/arena/current
[ -d "$R" ] || R=$(dirname "$(find / -name composer.json -path '*arena*' 2>/dev/null | head -1)")
cd "$R" || exit 1
# g [-i] PATTERN [DIR...] — ERE search in php/yaml/js/twig, vendor excluded, busybox-safe
g() { CI=""; [ "$1" = "-i" ] && { CI=-i; shift; }; P="$1"; shift; D="${*:-src config templates assets public/build}"
  find $D -type f \( -name '*.php' -o -name '*.yaml' -o -name '*.yml' -o -name '*.js' -o -name '*.twig' \) 2>/dev/null \
  | grep -v '/vendor/' | xargs grep -nE $CI "$P" 2>/dev/null | cut -c1-400 | head -${N:-150}; }
sec() { echo; echo "=================== $1"; }

sec "VERSION"; cat VERSION 2>/dev/null; grep -m1 '"version"' composer.json
sec "PHP ENCODED? (ionCube/SourceGuardian)"; grep -rlE 'ionCube|sg_load|SourceGuardian' src 2>/dev/null | head -3
sec "MERCURE CONFIG"; cat config/packages/mercure.yaml 2>/dev/null | sed -E 's/((secret|key|password|token)[^:]*:).*/\1 ***hidden***/I'; env | grep -i mercure | sed -E 's/^([^=]*(KEY|SECRET|PASSWORD|TOKEN)[^=]*=).*/\1***hidden***/I'
sec "MERCURE PUBLISH (new Update / publish / topics)"; N=300 g 'new Update\(|HubInterface|->publish\(|mercure|Mercure|topic'
sec "SCOREBOARD CONTROLLER (mercure token, fight open)"; f=$(grep -rlE 'mercuretoken|mercureToken' src 2>/dev/null | head -3); for x in $f; do echo "--- $x"; sed -n '1,400p' "$x"; done
sec "TIMER / CLOCK FIELDS"; N=200 g -i 'timer|clock|remainingTime|elapsed|roundTime|fightTime|duration' src/Entity
sec "UDP GATEWAY"; N=120 g -i 'udp|socket_|gateway' src config
sec "FIGHT ENTITY (fields + serializer groups)"; for x in src/Entity/Fight.php src/Entity/Fighter.php src/Entity/Round.php; do [ -f "$x" ] && { echo "--- $x"; grep -nE 'private|protected|#\[(Groups|SerializedName|ORM\\Column)|@(Groups|SerializedName|ORM\\Column)' "$x" | cut -c1-200; }; done
sec "SERIALIZER CONFIG"; ls config/serializer 2>/dev/null; for x in $(ls config/serializer/*Fight* config/serializer/*Fighter* 2>/dev/null); do echo "--- $x"; cat "$x"; done
sec "OAUTH / API KEY"; N=80 g -i 'grants/api_key|api_key|apiKey|client_credentials|oauth' src config
sec "JS: EventSource / mercure in frontend"; find public/build public/js assets -type f -name '*.js' 2>/dev/null | xargs grep -noE '.{0,120}(EventSource|\.well-known/mercure|topic=).{0,200}' 2>/dev/null | head -60
EOS

echo "done: $(wc -l < "$OUT") lines in $OUT (secrets hidden). Send this file."
