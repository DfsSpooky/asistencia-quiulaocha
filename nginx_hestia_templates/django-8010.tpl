
#=========================================================================#
# Default Web Domain Template                                             #
# DO NOT MODIFY THIS FILE! CHANGES WILL BE LOST WHEN REBUILDING DOMAINS   #
# https://hestiacp.com/docs/server-administration/web-templates.html      #
#=========================================================================#

server {
	listen      %ip%:%proxy_port%;
	server_name %domain_idn% %alias_idn%;
	root        %docroot%;
	index       index.php index.html index.htm;
	access_log  /var/log/nginx/domains/%domain%.log combined;
	access_log  /var/log/nginx/domains/%domain%.bytes bytes;
	error_log   /var/log/nginx/domains/%domain%.error.log error;

	include %home%/%user%/conf/web/%domain%/nginx.forbid.conf*;

	location ~ /\.(?!well-known\/) {
		deny all;
		return 404;
	}

    location /static/ {
        alias %home%/%user%/web/%domain%/public_html/static/;
        expires max;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location /media/ {
        alias %home%/%user%/web/%domain%/private/media/;
        expires 1h;
    }

	location / {
		proxy_pass              http://127.0.0.1:8010;
		proxy_set_header        Host $host;
		proxy_set_header        X-Real-IP $remote_addr;
		proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
		proxy_set_header        X-Forwarded-Proto $scheme;
		location ~* ^.+\.(%proxy_extentions%)$ {
			try_files  $uri @fallback;
			root       %docroot%;
			access_log /var/log/nginx/domains/%domain%.log combined;
			access_log /var/log/nginx/domains/%domain%.bytes bytes;
			expires    max;
		}
	}

	location @fallback {
		proxy_pass              http://127.0.0.1:8010;
		proxy_set_header        Host $host;
		proxy_set_header        X-Real-IP $remote_addr;
		proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
		proxy_set_header        X-Forwarded-Proto $scheme;
	}

	location /error/ {
		alias   %home%/%user%/web/%domain%/document_errors/;
	}

	include %home%/%user%/conf/web/%domain%/nginx.conf_*;
}
