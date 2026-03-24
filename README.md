# bugzilla_client

Auth-modell:

- `login` a belépéshez
- `query_user` csak akkor kell, ha a bug-szűréshez más érték kell
- jelszó `.secrets` fájlból vagy env változóból
- API key támogatott
- TUI + CLI ugyanarra az API-rétegre

## Ajánlott felépítés

### config.yaml

```yaml
bugzilla:
  url: "https://bugzilla.ceg.hu"
  login: "te@ceg.hu"
  password_env: "BUGZILLA_PASSWORD"

  review_fields:
    - cf_reviewers
    - cf_reviewer
```

Ha a query user eltér a logintól:

```yaml
bugzilla:
  url: "https://bugzilla.ceg.hu"
  login: "zsolt"
  query_user: "zsolt@ceg.hu"
  password_env: "BUGZILLA_PASSWORD"
```

## .secrets használat

```bash
cp .secrets.example .secrets
chmod 600 .secrets
```

Majd a `.secrets` tartalma:

```bash
BUGZILLA_PASSWORD="a_jelszavad"
```

A `run.sh` ezt automatikusan betölti.

## Indítás

```bash
cp config.example.yaml config.yaml
chmod +x run.sh
cp .secrets.example .secrets
chmod 600 .secrets
./run.sh check
./run.sh tui
```

## CLI példák

```bash
./run.sh check
./run.sh fields
./run.sh assigned --status NEW
./run.sh review --priority P1
./run.sh show 123456
./run.sh comment 123456 -m "Elkezdtem" --hours 1.0
```
 
## TUI

```bash
./run.sh tui
```

Billentyűk:

- `q` kilépés
- `r` frissítés
- `/` keresőmező fókusz
- `c` komment az aktuális bughoz
