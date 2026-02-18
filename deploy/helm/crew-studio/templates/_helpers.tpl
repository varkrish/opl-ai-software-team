{{/*
Expand the name of the chart.
*/}}
{{- define "crew-studio.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name (truncated to 63 chars).
*/}}
{{- define "crew-studio.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label value.
*/}}
{{- define "crew-studio.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels shared by every resource.
*/}}
{{- define "crew-studio.labels" -}}
helm.sh/chart: {{ include "crew-studio.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: {{ include "crew-studio.name" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Backend labels and selectors.
*/}}
{{- define "crew-studio.backend.labels" -}}
{{ include "crew-studio.labels" . }}
app.kubernetes.io/component: backend
{{- end }}

{{- define "crew-studio.backend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "crew-studio.fullname" . }}-backend
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: backend
{{- end }}

{{/*
Frontend labels and selectors.
*/}}
{{- define "crew-studio.frontend.labels" -}}
{{ include "crew-studio.labels" . }}
app.kubernetes.io/component: frontend
{{- end }}

{{- define "crew-studio.frontend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "crew-studio.fullname" . }}-frontend
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: frontend
{{- end }}

{{/*
Service account name.
*/}}
{{- define "crew-studio.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "crew-studio.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image pull secret list.
*/}}
{{- define "crew-studio.imagePullSecrets" -}}
{{- range .Values.global.imagePullSecrets }}
- name: {{ . }}
{{- end }}
{{- end }}
