{{- define "wordpress-clones.labels" -}}
app.kubernetes.io/name: wordpress-clones
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "wordpress-clones.name" -}}
{{- $slug := . -}}
{{- if gt (len $slug) 32 -}}
{{- printf "%s-%s-wp" ($slug | trunc 32 | trimSuffix "-") ($slug | sha256sum | trunc 8) -}}
{{- else -}}
{{- printf "%s-wordpress" $slug -}}
{{- end -}}
{{- end -}}

