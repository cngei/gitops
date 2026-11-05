# CNGEI WordPress clone Helm deployment

## Deploy with Helm

Prerequisites in the target cluster:

- External Secrets Operator serving `external-secrets.io/v1` ExternalSecrets,
  `external-secrets.io/v1alpha1` PushSecrets, Password generators, and a write-capable
  webhook provider. ESO must have its PushSecret controller enabled.
- MariaDB Operator and its Database, User and Grant CRDs.
- An existing `wordpress-multi-mariadb` MariaDB resource. Deploy the chart in
  the **same namespace** as this resource (`wordpress` in the reference repo).
- Traefik with `web`/`websecure` entry points and its CRDs.
- cert-manager and the `le-cert-issuer` ClusterIssuer.
- A suitable StorageClass and DNS records pointing each domain at Traefik.

WordPress pods and cron job pods are scheduled only on nodes labeled
`node-role.kubernetes.io/worker=wordpress`. This is the default `nodeSelector`
in `wordpress-multi/values.yaml` and can be overridden through Helm values.

1. The chart pins `ghcr.io/cngei/sezioni-wp` to
   `85a56f85791f66274c625f060b3ca8bc41a390b4`. Review the site list in
   `wordpress-multi/examples/values.yaml` and configure `imagePullSecrets` if required.
2. Set the mandatory `adminEmail` for every site and prepare the one-time
   Vaultwarden bridge credentials described below. ESO generates each site's
   `<subdomain>-wordpress-secret` automatically.
3. Render and install:

```sh
helm lint wordpress-multi -f wordpress-multi/examples/values.yaml
helm template clones wordpress-multi --namespace wordpress \
  -f wordpress-multi/examples/values.yaml
helm upgrade --install clones wordpress-multi --namespace wordpress \
  -f wordpress-multi/examples/values.yaml
```

Every site must be an object with a domain and a non-empty, valid `adminEmail`:

```yaml
sites:
  - domain: cn27.cngei.it
    adminEmail: cn27-admin@example.com
  - domain: roma.cngei.it
    adminEmail: roma-admin@example.com
```

There is no global email fallback. The configured Aosta address is
`paolo.campanelli@cngei.it`.

Use objects for overrides:

```yaml
sites:
  - domain: cn27.cngei.it
    adminEmail: cn27-admin@example.com
    persistence:
      size: 2Gi
  - domain: roma.cngei.it
    adminEmail: roma-admin@example.com
    persistence:
      size: 5Gi
      storageClass: longhorn-multi-wordpress
    database:
      name: wp_roma
      username: wp_roma
    secretRevision: "1"
```

Only single-label subdomains of `cngei.it` are accepted. Duplicate domains,
database names and database usernames are rejected. For each site, the chart
creates a PVC (default **2Gi**), one-replica Deployment, ClusterIP Service,
HTTP-to-HTTPS Middleware and IngressRoute, HTTPS IngressRoute, Certificate,
MariaDB Database/User/Grant, and a WordPress cron CronJob. With default secret
management, each site also gets two Password generators, an ExternalSecret,
and a PushSecret. One shared Vaultwarden bridge, namespaced SecretStore,
auth-token generator/ExternalSecret and NetworkPolicy are created per release.

Database names and usernames default to `wp_<subdomain>` with hyphens converted
to underscores. For example, `reggio-emilia.cngei.it` uses `wp_reggio_emilia`.
For a label longer than 61 characters, the default is the first 55 characters of
`wp_<normalized-subdomain>` plus `_` and the first eight hex characters of the
SHA-256 of the original subdomain. Explicit `database.name` and
`database.username` overrides are easier to use for such labels. Names must be
unique across all releases sharing the MariaDB cluster. Deploy a domain through
only one release.

### Generated credentials and Vaultwarden backup

ESO independently generates 40-character database and administrator passwords
for each site. `externalSecrets.password` controls the generator policy. The
ExternalSecret combines these with the domain, database identity and mandatory
site email into the existing `<subdomain>-wordpress-secret` contract. Optional
site fields are `adminUsername` (default `<subdomain>-admin`) and `siteTitle`
(default `CNGEI <subdomain>`). `externalSecrets.databaseHost` overrides the derived
MariaDB service address. Extra environment settings can be supplied through
`externalSecrets.extraEnv` or `sites[].extraEnv`; protected generated fields
cannot be replaced through these maps.

The ExternalSecrets use `refreshPolicy: CreatedOnce`, `immutable: true`, and
`creationPolicy: Orphan`. Helm also retains them by default. The passwords and
configuration are generated once, survive upgrades and uninstall, and are not
silently rotated when an ExternalSecret is recreated. An existing manually
created target Secret is retained with its current data and made immutable;
make sure it already contains all required fields before enabling generation.
Existing credentials/settings are not overwritten by changed Helm values.
To change them later, preserve the current passwords from Vaultwarden, replace
the Secret deliberately with the updated full data, reconcile the MariaDB user
if its password changed, and restart the WordPress Deployment. Do not delete a
live site's Secret to request a routine refresh: a missing Secret is generated
again with new passwords.

Each PushSecret sends the complete Secret as a JSON string map to the shared
bridge. The bridge saves a login item named `Kubernetes WordPress: <domain>`:
its login is the WordPress administrator, its URI points to `/wp-admin/`, its
custom fields include the database connection and password, and its notes hold
a JSON recovery copy of every environment key. Repeated pushes update the same
managed item, skip unchanged data, and reject ambiguous names or manually
created items with the same name. Removing the PushSecret retains the item in
Vaultwarden (`deletionPolicy: None`). The push is a backup; Vaultwarden is not
automatically used to recover a deleted Kubernetes Secret.

The bridge uses Bitwarden Password Manager's CLI API, which supports Vaultwarden.
It does not use the separate Bitwarden Secrets Manager product. The CLI image
is pinned to `2026.10.1` and binds its unlocked API to loopback inside the pod.
Only the authenticated adapter on port 8080 is exposed through a ClusterIP
Service. Its token is generated by ESO, and the NetworkPolicy admits only the
ESO controller's namespace and pod labels. Set
`vaultwarden.externalSecretsNamespace` and
`vaultwarden.externalSecretsPodSelector` to match your installed controller.
The internal HTTP connection uses this bearer token; the CLI connects to
`https://password.cngei.it` over TLS.

Create a dedicated Vaultwarden account for this integration, enable its personal
API key, and prepare three files in a private directory outside the repository:
`client-id`, `client-secret`, and `master-password`. Create the bootstrap Secret
in the chart's namespace before installing:

```sh
kubectl -n wordpress create secret generic wordpress-vaultwarden-credentials \
  --from-file=client-id=/secure/vaultwarden/client-id \
  --from-file=client-secret=/secure/vaultwarden/client-secret \
  --from-file=master-password=/secure/vaultwarden/master-password
```

The Secret name is configurable with `vaultwarden.credentialsSecret`. These
bootstrap values cannot be generated by ESO: they authenticate an existing vault
account. Store files privately and keep actual credentials out of Helm values
and Git. By default, entries belong to that account's personal vault. To put
entries in a shared organization, set `vaultwarden.organizationId` and one or
more `vaultwarden.collectionIds`; the account must be allowed to create and
edit items in those collections. `vaultwarden.folderId` optionally selects a
folder. The bridge has no Kubernetes API access.

For an existing external push store, disable `vaultwarden.enabled` and configure
`externalSecrets.pushSecret.secretStoreRef.name` and `.kind`. This replaces the
built-in bridge/store with the selected provider. To retain entirely manual
credential management, disable `externalSecrets.enabled`; to disable backup,
set `externalSecrets.pushSecret.enabled=false`. The manual Secret shape remains
in `wordpress-multi/examples/site-secret.yaml.example`.

Inspect reconciliation without printing credentials:

```sh
kubectl -n wordpress get externalsecret,pushsecret,secretstore
kubectl -n wordpress get deployments
```

Source references:
[ESO Password generator](https://external-secrets.io/latest/api/generator/password/),
[ExternalSecret lifecycle](https://external-secrets.io/main/api/externalsecret/),
[webhook push protocol](https://external-secrets.io/main/provider/webhook/), and
[Bitwarden Vault Management API](https://bitwarden.com/help/bitwarden-apis/).

### Secret contract

For `cn27.cngei.it`, the Secret must be named **`cn27-wordpress-secret`**, in the
release namespace. All per-site credentials/settings are loaded with `envFrom`.
The MariaDB User resource reads `WORDPRESS_DB_PASSWORD` from that same Secret.
ESO creates this Secret by default; create it manually only when generation is disabled.

| Secret key | Required | Meaning |
| --- | --- | --- |
| `WORDPRESS_DB_HOST` | Yes | MariaDB hostname, optionally `:port`; normally `wordpress-multi-mariadb.wordpress.svc.cluster.local:3306` |
| `WORDPRESS_DB_NAME` | Yes | Must match the chart's database name for this domain |
| `WORDPRESS_DB_USER` | Yes | Must match the chart's database username for this domain |
| `WORDPRESS_DB_PASSWORD` | Yes | Unique password shared by the MariaDB User resource and WordPress |
| `WORDPRESS_SITE_URL` | Yes | Exactly `https://<domain>`, without a trailing slash |
| `WORDPRESS_SITE_TITLE` | No | WordPress site title (`blogname`); template title is retained if omitted |
| `WORDPRESS_ADMIN_USER` | Yes | The sole administrator's username |
| `WORDPRESS_ADMIN_PASSWORD` | Yes | The sole administrator's password |
| `WORDPRESS_ADMIN_EMAIL` | Yes | Administrator and site notification email |
| `WORDPRESS_UPDATE_ON_START` | No | `true` by default; `false` skips the immediate core/plugin update only |
| `WORDPRESS_STRICT_PLUGIN_UPDATES` | No | `false` by default; `true` fails initialization on any plugin update failure |
| `WORDPRESS_DB_WAIT_TIMEOUT` | No | Seconds to wait for MariaDB and the grant; default `300` |
| `WORDPRESS_SOURCE_HOST` | No | Source template hostname; default `newsezioni.cngei.it` |

The chart supplies expected URL/database/user values as startup checks, and sets
`WORDPRESS_DISABLE_WP_CRON` according to `cron.enabled`. These checks stop a
mismatched Secret from initializing the wrong database. Database host values
support DNS names or IPv4 addresses, with an optional port, rather than Unix
sockets or IPv6 literals.

After deliberately replacing a Secret, increment that site's `secretRevision`
and sync/upgrade the release, or restart its Deployment. Environment variables are read at pod
creation. Database password rotation also requires the operator to reconcile its
User resource; the application waits for the new credentials to work.

### Initialization, updates and persistence

On each startup the image:

1. Locks the PVC and copies the template only when the volume is new.
2. Writes environment-based configuration and ensures fresh per-volume salts.
3. Waits for MariaDB and database grants. Imports the dump only into an empty
   database, or retries an import explicitly marked as interrupted.
4. Uses WP-CLI search/replace over **all tables in this site's database**, with
   PHP serialization handling, including custom tables and JSON-escaped URLs.
5. Sets the canonical URL and optional title. Creates/updates the requested
   administrator, removes every other user, and reassigns their content to it.
   Registration is disabled; existing administrator sessions and application
   passwords are cleared. These rules are reapplied on restart.
6. Enables auto-updates for all installed plugins. A managed must-use plugin also
   enables automatic stable core and plugin updates, including future plugins.
7. Updates WordPress core and all plugins, then flushes rewrites and object cache.

An independent database marker prevents accidental re-import even if the PVC is
lost. A nonempty database without this marker is rejected. Use a **new, dedicated
database** for each clone. Template files are copied again on a replacement PVC;
restore the clone's own volume backup to recover its later uploads and installed
or updated code. Back up MariaDB and the WordPress volume together.

WordPress core update failures stop startup. Plugin update failures are logged
and allow startup by default because the dump includes commercial/custom
plugins. Set `WORDPRESS_STRICT_PLUGIN_UPDATES=true` to require every available
plugin update to succeed. Premium plugins need valid licenses/update endpoints
for the new domain; a plugin without a published update cannot be upgraded by
WP-CLI. No plugin is removed or deactivated automatically.

In Kubernetes, a CronJob requests the site's internal `wp-cron.php` every five
minutes so scheduled core/plugin updates run even with no visitors. The job uses
HTTP inside the cluster; the public site uses TLS terminated by Traefik. Set
`cron.enabled=false` to use WordPress's normal visitor-triggered cron. The local
Compose setup uses normal WordPress cron.

The entire `/var/www/html` directory is persistent, allowing WordPress and plugin
updates to survive pod recreation. The Deployment uses `Recreate` and one
replica to avoid concurrent updates and ReadWriteOnce volume conflicts. Startup
probes allow up to 30 minutes for initial import and downloads. The image's
managed config, `.htaccess`, and update-policy plugin are refreshed at startup;
changing the image does not overwrite an existing site's core/plugins/uploads.

PVCs and MariaDB SQL resources carry `helm.sh/resource-policy: keep` by default;
the SQL CRs also use `cleanupPolicy: Skip`. Removing a domain or uninstalling a
release retains its data. Remove retained resources explicitly when retiring a
site. Configure `persistence.keep`, `mariadb.keepResources` and
`mariadb.cleanupPolicy` before uninstall if a different lifecycle is required.
PVC sizes can generally be increased if the StorageClass supports expansion;
shrinking an existing PVC is not supported by Kubernetes.

## Chart validation

```sh
helm lint wordpress-multi -f wordpress-multi/examples/values.yaml
python3 wordpress-multi/tests/test_chart.py
python3 wordpress-multi/tests/test_vaultwarden_bridge.py
```

Run the commands above from the GitOps repository root.

The HTTP bridge tests use a local Bitwarden API simulator and verify authentication,
key scoping, idempotent retries, updates, recovery payloads, conflicts and sanitized
errors. They do not log into the production Vaultwarden instance.
