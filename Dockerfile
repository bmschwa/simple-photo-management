FROM nginx:latest
RUN rm /etc/nginx/conf.d/default.conf
RUN mkdir -p /var/www/html/media
RUN mkdir /var/www/html/static
RUN mkdir /var/www/html/react
RUN mkdir /npm_build
RUN apt update && apt dist-upgrade -y
RUN apt install curl git -y
RUN curl -sL https://deb.nodesource.com/setup_12.x | bash -
RUN git clone --single-branch --branch frontend https://github.com/Aninstance/simple-photo-management.git /npm_build
WORKDIR  /npm_build/public
RUN apt install nodejs -y
RUN npm install
RUN npx npm-force-resolutions  # have to run this manually as won't work in package.json
RUN npm audit fix
WORKDIR /npm_build
COPY spm.conf /etc/nginx/conf.d/
COPY nginx.conf /etc/nginx/
COPY nginx-entrypoint.sh /
WORKDIR /
EXPOSE 80
ENTRYPOINT [ "/nginx-entrypoint.sh" ]
