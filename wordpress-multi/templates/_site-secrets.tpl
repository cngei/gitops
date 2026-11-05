{{- define "wordpress-clones.siteSecrets" -}}
{{- $root := .root -}}
{{- $name := .name -}}
{{- if $root.Values.externalSecrets.enabled -}}
{{- $databaseHost := default (printf "%s.%s.svc.cluster.local:3306" $root.Values.mariadb.name $root.Release.Namespace) $root.Values.externalSecrets.databaseHost -}}
{{- $data := dict
  "WORDPRESS_DB_HOST" $databaseHost
  "WORDPRESS_DB_NAME" .dbName
  "WORDPRESS_DB_USER" .dbUser
  "WORDPRESS_DB_PASSWORD" "{{ .databasePassword }}"
  "WORDPRESS_SITE_URL" (printf "https://%s" .domain)
  "WORDPRESS_SITE_TITLE" (default (printf "CNGEI %s" .slug) .site.siteTitle)
  "WORDPRESS_ADMIN_USER" (default (printf "%s-admin" (.slug | trunc 54 | trimSuffix "-")) .site.adminUsername)
  "WORDPRESS_ADMIN_PASSWORD" "{{ .adminPassword }}"
  "WORDPRESS_ADMIN_EMAIL" .adminEmail
-}}
{{- $extra := mergeOverwrite (deepCopy $root.Values.externalSecrets.extraEnv) (default dict .site.extraEnv) -}}
{{- range $key, $value := $extra -}}
  {{- if hasKey $data $key -}}{{- fail (printf "extraEnv must not replace generated setting %s" $key) -}}{{- end -}}
  {{- $_ := set $data $key $value -}}
{{- end -}}
{{- range $suffix := list "db-password" "admin-password" }}
{{ printf "\n---\n" }}
apiVersion: generators.external-secrets.io/v1alpha1
kind: Password
metadata:
  name: {{ $name }}-{{ $suffix }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    {{- include "wordpress-clones.labels" $root | nindent 4 }}
  {{- if $root.Values.externalSecrets.keep }}
  annotations:
    helm.sh/resource-policy: keep
  {{- end }}
spec:
  {{- toYaml $root.Values.externalSecrets.password | nindent 2 }}
{{- end }}
{{ printf "\n---\n" }}
apiVersion: {{ $root.Values.externalSecrets.apiVersion }}
kind: ExternalSecret
metadata:
  name: {{ $name }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    {{- include "wordpress-clones.labels" $root | nindent 4 }}
  {{- if $root.Values.externalSecrets.keep }}
  annotations:
    helm.sh/resource-policy: keep
  {{- end }}
spec:
  refreshPolicy: CreatedOnce
  target:
    name: {{ .secret }}
    creationPolicy: Orphan
    deletionPolicy: Retain
    immutable: true
    template:
      engineVersion: v2
      type: Opaque
      data:
        {{- toYaml $data | nindent 8 }}
  dataFrom:
    - sourceRef:
        generatorRef:
          apiVersion: generators.external-secrets.io/v1alpha1
          kind: Password
          name: {{ $name }}-db-password
      rewrite:
        - regexp:
            source: ^password$
            target: databasePassword
    - sourceRef:
        generatorRef:
          apiVersion: generators.external-secrets.io/v1alpha1
          kind: Password
          name: {{ $name }}-admin-password
      rewrite:
        - regexp:
            source: ^password$
            target: adminPassword
{{- end }}
{{- if $root.Values.externalSecrets.pushSecret.enabled }}
{{- $store := $root.Values.externalSecrets.pushSecret.secretStoreRef -}}
{{- $storeName := $store.name -}}
{{- if not $storeName -}}
  {{- if not $root.Values.vaultwarden.enabled -}}{{- fail "PushSecret requires vaultwarden.enabled or an external secretStoreRef.name" -}}{{- end -}}
  {{- $storeName = include "wordpress-clones.vaultwardenName" $root -}}
  {{- if ne $store.kind "SecretStore" -}}{{- fail "The built-in Vaultwarden store uses kind SecretStore" -}}{{- end -}}
{{- end }}
{{ printf "\n---\n" }}
apiVersion: {{ $root.Values.externalSecrets.pushSecret.apiVersion }}
kind: PushSecret
metadata:
  name: {{ .name }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    {{- include "wordpress-clones.labels" $root | nindent 4 }}
spec:
  refreshInterval: {{ $root.Values.externalSecrets.pushSecret.refreshInterval | quote }}
  updatePolicy: Replace
  deletionPolicy: None
  secretStoreRefs:
    - name: {{ $storeName }}
      kind: {{ $store.kind }}
  selector:
    secret:
      name: {{ .secret }}
  data:
    - match:
        # Omitting secretKey sends the full Secret as a JSON string map.
        remoteRef:
          remoteKey: {{ .domain | quote }}
{{- end }}
{{- end -}}
