# Simple Photo Management

***Due to lack of sponsorship/funding - and therefore assumed lack of interest - the GPL licensed version of this project offered here may no longer be regularly maintained as of this date.***

***I am continuing to maintain private source code and Docker repositories, serving regularly updated, prebuilt containers.***

***If anyone wants to avail of this project in production or would like to request other bespoke work on this project, please feel free to email me to discuss at productions@aninstance.com. Thank you!***

***Note: The GPL licensed version available here SHOULD NOT BE USED IN PRODUCTION (i.e. a "live" working environment) UNLESS whoever is administering it regularly patches project dependencies with upstream security updates as and when they are released by the vendors. In that case, if running with Docker, the Dockerfiles (both server & frontend) would need to be amended to pull from your forked and updated Github source.*** 

**Important: Before scanning an image directory, please ensure you have backed up your data. This is a beta product and should be used with that in mind. Please do not put your only copy of digital photos at risk!**

This a simple application built on web technologies to IPTC tag, search & image optimise digital photo libraries.

It has web frontend client that connects to a RESTful API backend. Data is stored in either a SQLite, mySQL or PostgreSQL (recommended) database.

The web client is built with React.js and the server backend is written in Python 3.6 using the Django framework.

## Screenshots

![Screenshot 1](./meta/img/screenshot_01.png?raw=true)

## Live Demo

There is a live demo available here:

http://staging-spm.aninstance.com

Login credentials are:

- Username: riker
- Password: z4Xd\*7byV\$xw

## Key features

- Recursively scan directories for digital image files
- Add, remove & edit IPTC meta keyword tags from digital images via a web interface
- Easily select previously used IPTC meta keyword tags and add to digital images
- Automatically create a range of smaller (optimised) versions of each larger image (e.g. .jpg from a .tiff)
- Optimised versions named using a hash of the origin image file, to prevent duplication (note, the hash includes metadata, so if an identical looking image has had a change in metadata it is deemed 'different'.)
- Display & download optimised & resized versions of large images in the web interface
- Database IPTC tags associated with each image
- Search for & display digital images containing single IPTC tags or a combination of multiple tags
- Search and replace IPTC tags over all scanned image directories
- Restrict access to specific tags for specific usernames
- Switch between `light` and `dark` modes (by setting an environment variable)

## Key technologies

- Python 3.7
- Django
- Django-rest-framework
- Javascript (ReactJS)
- HTML5
- CSS3

## Docker deployment

The `master` branch of this repository is source for the dockerised version of the server. Please checkout the `frontend` branch for source of the dockerised frontend web client.

If deploying with Docker, it is highly recommended to use Docker Compose. Please find an example docker-compose file (which builds the entire stack, including the web client & server) in the `master` (server) branch.

~~Prebuilt Docker images for server and client are available on DockerHub:~~

- Server:

  ~~URL: <https://hub.docker.com/r/aninstance/simple-photo-management>~~

  To pull the image:

  ~~`docker pull aninstance/simple-photo-management`~~

- Frontend client:

  ~~URL: <https://hub.docker.com/r/aninstance/simple-photo-management-client>~~

  To pull the image:

  ~~`docker pull aninstance/simple-photo-management-client`~~

To use this source code for non-dockerised builds, please amend the settings.py configuration file accordingly.

## Installation (on Linux systems)

**Note: These are basic instructions to install and run the app for demonstration purposes only and do not provide for a secure installation, such as would be required if the app was publicly available. Steps should be taken to harden the environment if using in production, such as applying suitable file & directory permissions; serving over a TLS connection; and running the Docker containers as a user other than root.**

To use the Docker images orchestrated with docker-compose:

- Create your app root directory & clone the repository into it:

  `mkdir spm`
  `cd spm`
  `git clone https://github.com/Aninstance/simple-photo-management.git .`

- Edit the following files to your specification:

  - `docker-compose-example.yml` - save as docker-compose.yml
  - `config/nginx/spm-example.config` - save as spm.conf
  - `config/.env.docker` - save as .env.docker (this is the frontend client configuration, where you may configure things like the number of items displayed per page)

  **Note: Don't forget to set the URL in both the `docker-compose.yml` (`app`'s `APP_URL` variable) and the `.env.docker` (`REACT_APP_ROUTE`, `REACT_APP_API_ROUTE` & `REACT_APP_API_DATA_ROUTE` variables) files (as above).**

- Create the following directories in the application's root directory. These are for persistent storage (i.e. they persist even after the app server & client containers have been stopped, started, deleted, upgraded):

  - `mkdir photo_directory` - this is the directory where copies of your original images will be stored.
  - `mkdir -p media/photos` - this is the directory where the processed images will be stored.
  - `mkdir -p media/photos_tn` - this is the directory where the processed thumbnail images will be stored.
  - `mkdir static` - this is the directory where static content will be stored (including the client code).
  - `mkdir postgres` - this is the directory where the database will be located.
  - `mkdir -p log/gunicorn` - this is the directory where the logs will be located.

- You may remove the `src` directory, since the source will already be installed in the Docker image.

- Run this command to pull the Docker images and start the server (which serves both the server & frontend client components):

  `docker-compose up --build --force-recreate -d`

- If running for the first time (i.e. your persistent database folder is empty), define a superuser & by issuing the following commands:

  - Note down the name of the server app (exposing port 8000) that is output in the following command (e.g. `spm_app_1`):

    `docker-compose ps`

  - Run the following, substituting `spm_app_1` with the correct name for the server app, as discussed above.

    `docker exec -it smp_app_1 python manage.py createsuperuser`

- If running for the first time, create an `administrators` group and add the new user to it, as follows:

  - Login at the django admin url - e.g. http://your_domain.tld/admin/
  - Click `add` next to `Groups` in the `Authentication & Authorization` section.
  - Name the new group `administrators`.
  - Under `Available permissions`, scroll to the bottom and select all the `spm_app` permissions, clicking the arrow on the right to add these to the `Chosen permissions` pane (you may hold `shift` to select multiple at once). Once done, click `Save`.
  - Navigate to `Home > Users > your username` and scroll down to the `Permissions` section. Select `administrators` from the `Available groups` box and double-click it. This moves it to `Chosen groups`. Scroll to the bottom of the page and click `Save`.
  - Click `LOG OUT` (top right)

- Copy your original images (or directories of images) into the `photo_directory` directory.

## Usage Instructions

- Navigate to the web client url - e.g. `http://your_domain.tld` **Note: When starting a newly built or pulled container for the first time, the web client may take several minutes (depending on your server's resources) to create a fresh build. You will get a `502 Bad Gateway` error whilst the NPM build is occurring. Please be patient and try refreshing the page in a few moments.**

- Login to the web client using the superuser credentials you'd previously supplied.
- Click on the `+` button to scan the photo_directory for new original photos. By default, this action:
  - Recursively scans for digital images (.jpg, .tiff, .png)
  - Reads any IPTC keyword tags and adds them to the database.
  - The digital images are processed, with a range of image sizes automatically generated.
- Give it a few seconds and click the green refresh button (far left of the toolbar, beneath the page numbers). Images with no pre-existing IPTC keyword tags should be displayed (if any).
- To display images that do have tags, try typing a phrase into the search bar:
  - To search for tags containing a specifically defined phrase, enclose the phrase between quotation marks, e.g. "a phrase tag"
  - To search for images that contain a combination of multiple tags, separate search words or phrases with either a space, or a forward slash `/`.
- Clicking the button with the `tag` icon re-scans all images in photo_directory, adds any newly discovered images and recopies all IPTC keyword tags to the database. To simply add new images without re-copying the tags, use the `+` button instead.
- Clicking the button with the `broom` icon cleans the database of references to any processed images that no longer exist in the `media` directories or the origin image `photo_directory`.
- Clicking the button with the `slashed 'T'` icon removes all tags from the database which are not currently attached to any image (tag pruning). Note, this *does not* remove tags from an image, but simply cleans database records of unused tags.
- Clicking the button with the `swap` icon (left & right arrows) switches to `search & replace` mode, which allows replacement of an IPTC tag in all images with another:
  - Simply enter the term to search for in the upper `Search` field, the replacement tag in the `Replace` field, then click on the red button to `search & replace`.
  - To remove a tag from all images *without replacing it with an alternative*, type a dash character (also known as minus or tack) "`-`" into the `Replace` field. Then click on the red button to delete that tag from every image in the database.
- Add new tags to an image in one of two ways. These actions both write the new tag(s) to the metadata of the **ORIGINAL IMAGE** and to the database.:
  - By entering them in the input field, in the `Action` column. Separate multiple tags with a `/`.
  - By selecting from the list of previously used tags, that appears below the input field after you've begun to enter your tag. As you continue to type, this list resolves to display tags containing a sequence of characters that match your input.
- Processed images may be operated on in the following ways:
  - Clicking on a thumbnail image opens a larger resolution copy of that processed image. This may then be downloaded, via the toolbar appearing at the top of the image viewer. Note, this is __not__ the *original* image, but rather a higher resolution version of the processed image.
  - The thumbnail images themselves have a mini-toolbar of buttons beneath them:
    - `Anticlockwise arrow` rotates the processed images (thumbnail and the generated larger versions up to 1080px wide) anticlockwise.
    - `Clockwise arrow` rotates the processed images (thumbnail and the generated larger versions up to 1080px wide) clockwise.
    - `Refresh symbol` reprocesses that image record. Processed images (thumbnail and larger versions up to 1080px wide) are generated and tags coped to them from the original image. This is useful if, for example, a processed image has been accidentally deleted, or corrupted for some reason.
- Occasionally, the user may encounter errors during tag writing, due to problematical or corrupted preexisting metadata. In that case, a `mod_lock` flag is set in the database and the metadata of the original image is no longer allowed to be modified by the system until this flag has been removed. Clicking on the `red button with a crossed 'T' & padlock icon` will bulk remove *ALL* metadata from *ALL* original images that have the `mod_lock` flag set. Once the problematical metadata on an image has been deleted, the `mod_lock` flag is removed. Instigating this function should obviously be done with caution.
- To restrict access to specific tags for specific usernames, log in to the Django admin dashboard (typically found at `http://your_api_domain.tld/admin`). After logging in, navigate to the `SPM_APP > Photo Tag` page. Once there, you may select tags and assign them to specific usernames.
Note: the users would need to have already been created - however they should have *no* administrative privileges, nor be assigned to the `administrators` group, since there would otherwise be no point in providing access to specific tags, because users in the `administrators` group have read/write permissions on all records in any case. Non-administrator usernames have access to *only those tags which have been assigned to those usernames*.
  
## Documentation

The above guide is not definitive and is intended for users who know their way around Docker (and know how to troubleshoot!) If there are enough users of this app to warrant it, more thorough documentation would likely be made available. In the meantime, usage or installation questions can be sent to the contact details below.

## Development Roadmap

- ~~Automated display of tag suggestions (based on real-time character matching & most used) when adding IPTC tags to an image~~ [Complete]
- Enhancement of `clean` to facilitate deletion of processed image files & thumbnails (rather just database entries) when origin image no longer exists
- Tag suggestions based on facial recognition
- Expose switching between `light` and `dark` modes on the UI rather than requiring setting of environment variable
- ~~Remove tags from the used-tag list if no longer used for any images~~ [Complete]
- ~~Add ability to delete tags after search (currently limited to replace with an alternative tag)~~ [Complete]

## Support

- Paid support services (including installation, configuration and development of bespoke features) are available. Please email productions@aninstance.com with "Simple Photo Management Support" in the subject field.

## Authors

- Dan Bright (Aninstance Consultancy), productions@aninstance.com
