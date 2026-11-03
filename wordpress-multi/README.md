# CNGEI WordPress clone Helm deployment

## Deploy with Helm

Prerequisites in the target cluster:

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
2. Create an externally managed Secret for every site. See
   `wordpress-multi/examples/site-secret.yaml.example`; the chart never creates credentials.
3. Render and install:

```sh
helm lint wordpress-multi -f wordpress-multi/examples/values.yaml
helm template clones wordpress-multi --namespace wordpress \
  -f wordpress-multi/examples/values.yaml
helm upgrade --install clones wordpress-multi --namespace wordpress \
  -f wordpress-multi/examples/values.yaml
```

The minimal site list accepts strings:

```yaml
sites:
  - cn27.cngei.it
  - roma.cngei.it
```

Use objects for overrides:

```yaml
sites:
  - domain: cn27.cngei.it
    persistence:
      size: 2Gi
  - domain: roma.cngei.it
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
MariaDB Database/User/Grant, and a WordPress cron CronJob.

Database names and usernames default to `wp_<subdomain>` with hyphens converted
to underscores. For example, `reggio-emilia.cngei.it` uses `wp_reggio_emilia`.
For a label longer than 61 characters, the default is the first 55 characters of
`wp_<normalized-subdomain>` plus `_` and the first eight hex characters of the
SHA-256 of the original subdomain. Explicit `database.name` and
`database.username` overrides are easier to use for such labels. Names must be
unique across all releases sharing the MariaDB cluster. Deploy a domain through
only one release.

### Secret contract

For `cn27.cngei.it`, the Secret must be named **`cn27-wordpress-secret`**, in the
release namespace. All per-site credentials/settings are loaded with `envFrom`.
The MariaDB User resource reads `WORDPRESS_DB_PASSWORD` from that same Secret.

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

After changing a Secret, increment that site's `secretRevision` and sync/upgrade
the release, or restart its Deployment. Environment variables are read at pod
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
```

Run the commands above from the GitOps repository root.
