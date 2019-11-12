import logging
from django.contrib.auth.models import User, Group
from django.http import JsonResponse
from django.utils.datetime_safe import datetime
from rest_framework.views import APIView
import os
from .custom_validators import (RequestQueryValidator, validate_search, validate_tag_list,
                                validate_update_mode, validate_rotation_degrees)
from django.core.exceptions import ValidationError
from rest_framework import (viewsets, permissions, serializers, status)
from .serializers import (
    UserSerializer,
    GroupSerializer,
    ChangePasswordSerializer,
    PhotoDataSerializer,
    PhotoTagSerializer
)
from .custom_permissions import AccessPermissions
from .models import PhotoData, PhotoTag
from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from .spm_worker.process_images import ProcessImages
from django_q.tasks import async_task, result
from functools import partial
from django.conf import settings
import time
import re
import uuid

"""
Note about data object (database record):
Record referenced by URL param can be accessed in create & update methods through: self.get_object()

Note about permissions:
If viewset passed into class is ModelViewSet rather than a permission restricted one such as
ReadOnlyModelViewset, then permission classes can be set within the class,
via the 'permission_classes' class attribute.

Permissions classes include:
    Defaults: permissions.IsAuthenticated, permissions.IsAuthenticatedOrReadOnly, permissions.IsAdmin
    My custom: IsOwnerOrReadOnly

Need to be put in a set, e.g. permission_classes = (permissions.IsAuthenticated, IsOwnerOrReadOnly).
If only one, leave trailing comma e.g.(permissions.IsAuthentication,)
"""

# Get an instance of a logger
logger = logging.getLogger('django')


class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoints for users
    """

    """
    Includes by default the ListCreateAPIView & RetrieveUpdateDestroyAPIView
    (i.e. provides users-list and users-detail views, accessed by path, & path/<id>).
    """
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    # overrides default perm level, set in settings.py
    permission_classes = (permissions.IsAdminUser,)


class GroupViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows groups to be viewed or edited.
    """
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = (permissions.IsAdminUser,)


class PasswordUpdateViewSet(viewsets.ModelViewSet):
    """
    API endpoint for updating password
    """
    queryset = User.objects.all()
    serializer_class = ChangePasswordSerializer
    permission_classes = (AccessPermissions,)
    lookup_field = 'username'

    def perform_update(self, serializer):
        """
        override perform_update to perform any additional filtering, modification, etc
        """
        super().perform_update(serializer)


class Logout(APIView):
    def post(self, request, format=None):
        logger.info(request.user)
        try:
            # delete the token to force a login
            request.user.auth_token.delete()
            return Response({
                'success': True,
                'logged_in': False,
                'error': None,
                'user_is_admin': False},
                status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(e)
            return Response({
                'success': False,
                'logged_in': True,
                'error': str(e),
                'user_is_admin': False
            },
                status=status.HTTP_400_BAD_REQUEST)


class PhotoDataViewSet(viewsets.ModelViewSet):
    """
    API endpoints for PhotoData
    """

    """
    Includes by default the ListCreateAPIView & RetrieveUpdateDestroyAPIView
    i.e. provides photodata-list and photodata-detail views, accessed by path, & path/<id>)
    """
    queryset = PhotoData.objects.all()
    serializer_class = PhotoDataSerializer
    permission_classes = (AccessPermissions,)

    """
    Note on permissions:
    Access control is dealt with in 2 places: here (views.py) and serializers.py.

        - views.py: basic 1st hurdle checks, performed before input validation, 
        including whether requester user level has access to models, and whether has ability to 
        preform actions on model objects. This is via permission_classes 
        (may call access permissions classes from ./custom_permissions.py or default access permission 
        classes from DRF.

        - serializers.py: 2nd hurdle checks - more complicated checks performed after input validation 
        but before changes committed to the model. E.g. ensuring only certain user levels are able 
        to update specific fields in certain ways.    
    """

    def get_queryset(self):
        records = []
        # order queryset using request query (or 'id' by default if no order_by query)
        all_records = self.queryset.order_by(RequestQueryValidator.validate(
            RequestQueryValidator.order_by, self.request.query_params.get(
                'order_by', None)
        ))
        # set username of requester to user attr of serializer to allow return admin status in response
        self.serializer_class.user = self.request.user
        # handle search queries, if any
        search_query = self.request.query_params.get('tag', None)
        if search_query:
            try:
                records = self.handle_search(
                    records=all_records, search_term=search_query)
            except ValidationError as e:
                # if invalid search char, don't return error response, just return empty
                raise serializers.ValidationError(
                    detail=f'Validation error: {e}')
        else:
            records = all_records.filter(tags=None).distinct()
        return records  # return filtered records, or empty list if no incoming search query

    def perform_create(self, serializer_class):
        """
        override perform_create to save the user as the owner of the record
        """
        serializer_class.save(owner=self.request.user)

    def perform_update(self, request, *args, **kwargs):
        """
        override perform_update to perform any additional filtering, modification, etc
        """
        # define eligible update fields (useful to quickly disable updating of fields if required)
        eligible_update_fields = {'tags'}
        updated_record = None
        updated_instance = dict()
        try:  # first validate incoming data for fields eligible for update
            if 'tags' in eligible_update_fields:  # tags
                try:
                    validate_tag_list(request.data['tags'])
                    tags_to_update = request.data['tags']
                except ValidationError as e:
                    raise serializers.ValidationError(
                        detail=f'Validation error: {e.message}')
            # validate update mode param
            validate_update_mode(request.data.get('update_mode', None))
            update_mode = request.data['update_mode']
            if update_mode == 'add_tags':
                updated_instance = self.handleAddTags(
                    record_id=kwargs['pk'], tags=tags_to_update, user=request.user)
            elif update_mode == 'remove_tag':
                updated_instance = self.handleRemoveTags(record_id=kwargs['pk'], tags=tags_to_update,
                                                         user=request.user)
            elif update_mode == 'rotate_image':
                # validate rotation degree
                validate_rotation_degrees(
                    request.data['update_params']['rotation_degrees'])
                degrees = request.data['update_params']['rotation_degrees']
                # rotate the image
                updated_instance = self.handleMutateImage(record_id=kwargs['pk'], user=request.user,
                                                          mutation={'rotation': {'degrees': degrees}})
            if updated_instance['success']:
                new_data = updated_instance['data']
                updated_record = {
                    'id': new_data.id,
                    'owner': request.user.username,
                    'file_name': new_data.file_name,
                    'file_format': new_data.file_format,
                    'processed_url': new_data.processed_url,
                    'original_url': new_data.original_url,
                    'public_img_url': new_data.public_img_url,
                    'public_img_tn_url': new_data.public_img_tn_url,
                    'tags': [t for t in new_data.tags.all().values_list('tag', flat=True)],
                    'record_updated': new_data.record_updated,
                    'user_is_admin': request.user.groups.filter(name='administrators').exists(),
                    'uuid': uuid.uuid4().hex  # add UUID to ensure caches can be cleared for new img
                }
            return JsonResponse(data=updated_record, status=status.HTTP_202_ACCEPTED) if updated_instance['success'] else JsonResponse(
                {"status": f"Nothing was updated: {updated_instance['data']}"}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception as e:
            return JsonResponse({"status": f"Error: {e}"}, status=422)
        # return JsonResponse(JSONRenderer().render(serializer.data), status=202) if added else JsonResponse(
        #     {'Status': 'Error whilst adding tags!'}, status=500)

    def perform_destroy(self, instance):
        # only allow admins to delete objects
        if self.request.user.groups.filter(name='administrators').exists():
            super().perform_destroy(instance)
        else:
            raise serializers.ValidationError(
                detail='You are not authorized to delete photo data!')

    @staticmethod
    def handle_search(records: queryset, search_term: str) -> queryset:
        """function to handle search
        :param all_records: queryset of all records
        :param search_term: search term string
        :return: queryset of filtered results
        """
        search_query = validate_search(search_term)
        # create tuple of search tags, split by "/" character in search string
        terms = tuple(search_query.split('/'))
        for t in terms:
            # special search to allow searching for erroneous whitespace tag
            if t == '-SPACE-':
                return records.filter(tags__tag='').distinct()
            # regular search
            records = records.filter(
                Q(tags__tag__icontains=t)).distinct() if t else records
        return records

    def handleMutateImage(self, record_id: int, user: User, mutation: dict) -> dict:
        """function to handle mutating the PROCESSED image.
        - Tags are copied from the original version to the newly mutated image.
        - Once image mutated, new thumbnails are recreated.
        :param origin_file_url: str: url of the image file to rotate
        :param mutation: dict: dict of mutation actions, in form {'the_mutation':{'mutation_param': 'param_value'}} 
            e.g.: {'rotation':{'degrees': 90}}
        :return: dict: {'success': True|False, 'data': Modified PhotoData instance | str: fail message}
        """
        try:
            record = PhotoData.objects.get(id=record_id)
            path = os.path.split(record.original_url)[0]
            conversion_format = settings.SPM["CONVERSION_FORMAT"]
            orig_filename = os.path.split(record.original_url)[1]
            save_path = settings.SPM['PROCESSED_IMAGE_PATH']
            thumb_path = settings.SPM['PROCESSED_THUMBNAIL_PATH']
            conversion_generated = mutated = False
            error_message = ''
            # first, check modification lock, to guard against concurrent image modifications
            if not record.mod_lock:
                # set the mod_lock
                record.mod_lock = True
                record.save()
                # do the mutation
                if 'rotation' in mutation.keys():
                    mutated = ProcessImages.rotate_image(
                        origin_file_url=record.processed_url,
                        rotation_degrees=mutation['rotation']['degrees'],
                        copy_tags=True, thumb_path=thumb_path,
                        save_path=save_path, save_format=conversion_format, thumb_sizes=settings.SPM['THUMB_SIZES'])
                # generate converted image & thumbs
                if mutated:  # save to db model
                    record.record_updated = datetime.utcnow()
                    # unlock the mod lock
                    record.mod_lock = False
                    # save the model
                    record.save()
                    conversion_generated = True
            else:
                error_message = 'MUTATION FAILED: Modification locked for this image!'
                logger.info(error_message)
        except PhotoData.DoesNotExist:
            error_message = 'The requested photo record does not exist in the database!'
            logger.error(error_message)
        except Exception:
            error_message = 'An error occurred whilst attempting to rotate the image!'
            logger.error(error_message, exc_info=True)
        return {
            'success': True if mutated and conversion_generated else False,
            'data': record if mutated and conversion_generated else error_message}

    def handleRemoveTags(self, record_id: int, tags: [str], user: User, write_to_iptc: bool = True,
                         iptc_key: str = 'Iptc.Application2.Keywords') -> bool:
        """function to delete tags from origin images.
        - Compiles ammended tag list (orginal tags minus removed) to origin image
        - Calls handleAddTags to write updated tags to origin image & generate new 
            converted images (database then updated via the calling perform_update method)
        :param record_id: ID of record to be updated
        :param tags: List of tags (strings) to remove from the image
        :param user: user doing the updating
        :param write_to_iptc: boolean: whether to delete the tags from image (not only db record)
        :param iptc_key: str: IPTC key. Defaults to keyword (Iptc.Application2.Keywords)
        :return: return of handleAddTags function, in form: 
            dict: {'success': True|False, 'data': Modified PhotoData instance | str: fail message}
        """
        # get model instance to update
        record = PhotoData.objects.get(id=record_id)
        # set modification lock
        record.mod_lock = True
        record.save()
        # create new converted images with the requested tags removed
        updated_tags = set(t.tag for t in record.tags.all()) - set(tags)
        # write new tag list to origin image
        return self.handleAddTags(record_id=record_id, tags=list(updated_tags), user=user, write_to_iptc=write_to_iptc,
                                  iptc_key=iptc_key, retain_original=False)

    @staticmethod
    def handleAddTags(record_id: int, tags: [str], user: User, write_to_iptc: bool = True,
                      iptc_key: str = 'Iptc.Application2.Keywords', retain_original: bool = True,
                      processed_only: bool = False) -> PhotoData or bool:
        """function to add tags to the PhotoData model
        :param record_id: ID of record to be updated
        :param tags: List of tags (strings) to add to the existing tags
        :param user: user doing the updating
        :param write_to_iptc: boolean: whether to write the new tags to the image
        :param iptc_key: str: IPTC key. Defaults to keyword (Iptc.Application2.Keywords)
        :param retain_original: bool: whether to retain original tags or simply replace with new
        :param processed_only: bool: whether to only write tags to processed image. False also writes to origin.
        :return: dict: {'success': True|False, 'data': Modified PhotoData instance | str: fail message}
        """
        # get model instance to update
        record = PhotoData.objects.get(id=record_id)
        error_message = ''
        renamed_main = False
        # set modification lock
        record.mod_lock = True
        record.save()
        # remove any empty tags
        tags = list(filter(None, tags))
        # update the tag model with new tags, if necessary
        if write_to_iptc:
            try:
                origin_file_url = record.original_url
                processed_image_path = settings.SPM['PROCESSED_IMAGE_PATH']
                thumb_path = settings.SPM['PROCESSED_THUMBNAIL_PATH']
                conversion_format = settings.SPM['CONVERSION_FORMAT']
                tags_to_add = {'iptc_key': iptc_key, 'tags': tags}
                logger.info(f'TAGS TO REPLACE WITH: {tags}')
                # write tags to the origin image
                if not processed_only:
                    ProcessImages.add_tags(target_file_url=origin_file_url, tags=tags_to_add,
                                           retain_original=retain_original)
                # write to processed image
                ProcessImages.add_tags(target_file_url=record.processed_url, tags=tags_to_add,
                                       retain_original=retain_original)
                # rename processed file so name matches new hash of origin image
                try:
                    renamed_main = ProcessImages.rename_image(url_file_to_hash=origin_file_url,
                                                              url_file_to_rename=record.processed_url, with_hash=True)
                except Exception:
                    logger.error(
                        'Renaming the processed file failed!', exc_info=True)
                # rename thumbs as above
                if renamed_main:
                    path, new_file = os.path.split(renamed_main)
                    new_filename, new_format = os.path.splitext(new_file)
                    old_filename = record.file_name
                    old_format = record.file_format
                    for tn in settings.SPM['THUMB_SIZES']:
                        if thumb_path:
                            old_url = os.path.join(
                                thumb_path, f'{old_filename}-{"_".join((str(t) for t in tn))}{old_format}')
                            new_name = f'{new_filename}-{"_".join((str(t) for t in tn))}{new_format}'
                            try:
                                ProcessImages.rename_image(
                                    url_file_to_rename=old_url, new_name=new_name)
                            except Exception:
                                logger.error(
                                    'Renaming the thumbnail file failed!', exc_info=True)
            except Exception:
                error_message = 'An error occurred whilst attempting to add tags'
                logger.error(error_message, exc_info=True)
        # write tags to db only if successfully written to image (if required)
        try:
            successfully_added_tags = []
            for tag in tags:
                try:
                    tag, tag_created = PhotoTag.objects.get_or_create(
                        tag=tag, defaults={'owner': user})
                    successfully_added_tags.append(tag)
                except Exception as e:
                    error_message = 'An exception occurred whilst attempting to save tags to database!'
                    logger.warning(error_message, exc_info=True)
            # save tags & updated image data to PhotoData model
            if retain_original:
                [record.tags.add(t) for t in successfully_added_tags]
            else:
                record.tags.set(successfully_added_tags)
            record.record_updated = datetime.utcnow()
            # update db with new processed image filenames
            if renamed_main:
                record.file_name = new_filename
                record.file_foramt = new_format
                record.processed_url = os.path.join(
                    processed_image_path, new_filename + new_format)
            # release modification lock
            record.mod_lock = False
            # save the model
            record.save()
            return {'success': True, 'data': record}
        except Exception as e:
            error_message = e
            logger.error(error_message)
        # release modification lock
        record.mod_lock = False
        record.save()
        return {'success': False, 'data': error_message}


class PhotoTagViewSet(viewsets.ModelViewSet):
    """
    API endpoints for PhotoTag
    """

    """
    Includes by default the ListCreateAPIView & RetrieveUpdateDestroyAPIView
    i.e. provides phototag-list and phototag-detail views, accessed by path, & path/<id>)
    """
    queryset = PhotoTag.objects.all()
    serializer_class = PhotoTagSerializer
    permission_classes = (AccessPermissions,)

    """
    Note on permissions:
    Access control is dealt with in 2 places: here (views.py) and serializers.py.

        - views.py: basic 1st hurdle checks, performed before input validation, 
        including whether requester user level has access to models, and whether has ability to 
        preform actions on model objects. This is via permission_classes 
        (may call access permissions classes from ./custom_permissions.py or default access permission 
        classes from DRF.

        - serializers.py: 2nd hurdle checks - more complicated checks performed after input validation 
        but before changes committed to the model. E.g. ensuring only certain user levels are able 
        to update specific fields in certain ways.    
    """

    def get_queryset(self):
        # order queryset using request query (or 'id' by default if no order_by query)
        records = self.queryset.order_by(RequestQueryValidator.validate(
            RequestQueryValidator.order_by, self.request.query_params.get(
                'order_by', None)
        ))
        # set username of requester to user attr of serializer to allow return admin status in response
        self.serializer_class.user = self.request.user
        try:
            if 'term' in self.request.query_params and self.request.query_params.get('term', None):
                records = self.handle_search(
                    all_records=records, search_term=self.request.query_params.get('term'))
        except ValidationError as e:
            # if invalid search char, don't return error response, just return empty
            logger.info(f'Returning no results in response because: {e}')
            records = []
        return records  # return everything

    @staticmethod
    def handle_search(all_records: queryset, search_term: str) -> queryset:
        """method to handle search
        :param all_records: queryset of all records
        :param search_term: search term string
        :return: queryset of filtered results
        """
        search_query = validate_search(search_term)
        records = all_records.filter(
            Q(tag__icontains=search_query) if search_query else None).distinct()
        return records

    def perform_create(self, serializer_class):
        """
        override perform_create to save the user as the owner of the record
        """
        serializer_class.save(owner=self.request.user)

    def perform_update(self, serializer):
        """
        override perform_update to perform any additional filtering, modification, etc
        """
        super().perform_update(serializer)  # call to parent method to save the update

    def perform_destroy(self, instance):
        # only allow admins to delete objects
        if self.request.user.groups.filter(name='administrators').exists():
            super().perform_destroy(instance)
        else:
            raise serializers.ValidationError(
                detail='You are not authorized to delete photo data!')


class ProcessPhotos(APIView):
    """
    API endpoint that allows tags to be read from photos
    and added to the database
     """

    permission_classes = (permissions.IsAuthenticated,)

    def __init__(self):
        super().__init__()

    @staticmethod
    def add_record_to_db(record, owner, resync_tags=False):
        """
        method to add images to the database model
        :param record: dict of saved conversion data and tags: e.g.:
            {conversion_data: {'orig_path': '/path/to/orig/image', 'processed_path':'/path/to/processed_image',
            'filename': '4058.jpeg'}, tag_data: {'iptc_key': 'Iptc.Application2.Keywords', 'tags':
            ['DATE: 1974', 'PLACE: The Moon']}
        :param owner: current user
        :param resync_tags: whether embedded IPTC tags were re-copied from image file to the PhotoData model
        :return: saved record | False
        """
        try:
            updated_tags = []
            try:
                orig_filename = record['conversion_data']['orig_filename']
                new_filename = record['conversion_data']['new_filename']
                original_path = record['conversion_data']['orig_path']
                processed_path = record['conversion_data']['processed_path']
                logger.info(f'NEW FILENAME: {new_filename}')
                photo_data_record, new_record_created = PhotoData.objects.update_or_create(
                    original_url=os.path.join(original_path, orig_filename),
                    defaults={
                        'owner': owner,
                        'file_name': os.path.splitext(new_filename)[0],
                        'file_format': os.path.splitext(new_filename)[1],
                        'processed_url': os.path.join(processed_path, new_filename),
                        'public_img_url': os.path.normpath(settings.SPM['PUBLIC_URL']),
                        'public_img_tn_url': os.path.normpath(settings.SPM['PUBLIC_URL_TN'])
                    })
            except Exception as e:
                new_record_created = False
                photo_data_record = None
                logger.info(
                    f'Did not save image data to the database: {e}')
            """
            if new image data was created - or resync_tags=True - , create PhotoTag objects
            (creating in the model if necessary with update_or_create), then populate saved
            PhotoData model's M2M tags field with that list (& save again the now newly tagged model). 
            Then, add image data to a list for return
            """
            if photo_data_record and (new_record_created or resync_tags):
                if record['tag_data']['tags']:
                    for tag in record['tag_data']['tags']:
                        try:
                            tag, tag_created = PhotoTag.objects.get_or_create(
                                tag=tag, defaults={'owner': owner})
                            updated_tags.append(tag)
                        except Exception as e:
                            logger.warning(
                                f'An exception occurred whilst attempting to save tags to database: {e}')
                    # save tags to PhotoData model
                    photo_data_record.tags.set(updated_tags)
                    photo_data_record.record_updated = datetime.utcnow()
                    # save the model
                    photo_data_record.save()
                else:
                    photo_data_record.tags.clear()  # if no tags, clear any that were preexisting
                logger.info(f'Added record: {photo_data_record}')
                return photo_data_record
        except Exception as e:
            logger.warning(
                f'An exception occurred whilst attempting to save photos to database: {e}')
        return False

    @staticmethod
    def delete_record(record_id):
        """method to delete record from database
        :param record_id: id of record to be deleted
        :return: True|False
        """
        PhotoData.objects.get(id=record_id).delete()
        return True

    @staticmethod
    def clean_database(owner, origin_directories=False, processed_directories=False):
        """
        function to purge database of records referring to files that 
        no longer exist in the image directories
        """
        orphaned_processed_set = set()
        orphaned_no_origin_set = set()
        if processed_directories:
            url_list_generator = ProcessImages.file_url_list_generator(directories={settings.SPM['PROCESSED_IMAGE_PATH']},
                                                                       recursive=False)
            filenames_set = {os.path.splitext(os.path.split(f)[1])[
                0] for f in url_list_generator}
            orphaned_processed_set = set(PhotoData.objects.values_list(
                'file_name', flat=True).all()) - filenames_set
        if origin_directories:
            url_list_generator = ProcessImages.file_url_list_generator(
                directories=settings.SPM['ORIGIN_IMAGE_PATHS'], recursive=True)
            # combine sets using the "|" set union operator
            origin_url_set = {f for f in url_list_generator}
        if origin_url_set:
            orphaned_no_origin_set = set(PhotoData.objects.values_list(
                'original_url', flat=True).all()) - origin_url_set
        # combine sets using the "|" set union operator
        all_orphaned_records = orphaned_no_origin_set | orphaned_processed_set
        logger.info(f'ALL ORPHANED RECORDS: {all_orphaned_records}')
        if all_orphaned_records:
            for record in all_orphaned_records:
                try:
                    PhotoData.objects.filter(
                        Q(file_name=record) | Q(original_url=record)).delete()
                    logger.info(f'RECORD TO DELETE: {record}')
                except Exception as e:
                    logger.error(f'Error in clean_database: {e}')
        return True

    @staticmethod
    def process_images(retag=False, clean_db=False, scan=False, user=None):
        """
        method to process images (make resized copy (i.e. a 'processed' copy) & copy tags from original to resized)
        :param retag: whether to re-copy over tags from original to processed image, if filename already exists
        :param user: current user
        :param add_record_to_db: function, that submits records to DB model
        :return: True | False
        """
        origin_image_paths = settings.SPM['ORIGIN_IMAGE_PATHS']
        processed_image_path = settings.SPM['PROCESSED_IMAGE_PATH']
        thumb_path = settings.SPM['PROCESSED_THUMBNAIL_PATH']
        conversion_format = settings.SPM['CONVERSION_FORMAT']
        try:
            # if action is to scan origin dirs for new files or retag existing processed files
            if scan or retag:
                # initiate a ProcessImages object
                image_processor = ProcessImages(origin_image_paths=origin_image_paths,
                                                processed_image_path=processed_image_path,
                                                thumb_path=thumb_path,
                                                conversion_format=conversion_format,
                                                retag=retag,
                                                thumb_sizes=settings.SPM['THUMB_SIZES'])
                # get reference to the generator that processes images
                process_images_generator = image_processor.process_images()
                if process_images_generator:
                    # iterate generator & pass records to function that submits them to DB model
                    """
                    # debug: test iterating generated values, one at a time
                    iterator = iter(process_images_generator)
                    print(next(iterator))
                    print(next(iterator))
                    """
                    for processed_record in process_images_generator:
                        # pause if using sqlite to avoid db lock during concurrent writes
                        if hasattr(settings, 'RUN_TYPE'):
                            if settings.RUN_TYPE == settings.RUN_TYPE_OPTIONS[0]:
                                time.sleep(.300)
                        # kick off async task to add records to database model
                        async_task(ProcessPhotos.add_record_to_db, record=processed_record,
                                   owner=user, resync_tags=retag)
                else:
                    logger.error(
                        f'An error occurred during image processing. Operation cancelled.')
                    return False
                return True
            # if action is to clean the database of obsolete image data (i.e. records referring to deleted images)
            if clean_db:
                async_task(ProcessPhotos.clean_database, owner=user,
                           origin_directories=True, processed_directories=True)
        except (ValidationError, Exception) as e:
            if isinstance(e, ValidationError):
                logger.error(f'Validation error: {e.message}')
            else:
                logger.error(f'Error in processing: {e}')
        return False

    def get(self, request):
        """
        hand off the image processing and tagging task to django_q multiprocessing (async)
        """
        action_queries = {
            'scan': 'Scan the origin directories for new files, create web copies & copy tags from origin to processed images.',
            'retag': 'Retag copied (processed) image files with tags on the origin image.',
            'clear_db': 'Remove database records relating to images that have been removed from origin directories.'
        }
        try:
            # check for request queries - & validate - that indicate required action on data
            scan = RequestQueryValidator.validate(
                'bool_or_none', self.request.query_params.get('scan', None))
            retag = RequestQueryValidator.validate(
                'bool_or_none', self.request.query_params.get('retag', None))
            clean_db = RequestQueryValidator.validate(
                'bool_or_none', self.request.query_params.get('clean_db', None))
            """if at least 1 request query (query_params dict key) exists as a valid action query (action_queries dict key)
            kick off the main async task to process next step.
            """
            if set(action_queries.keys()).intersection(self.request.query_params.keys()):
                async_task(ProcessPhotos.process_images, retag=retag,
                           user=self.request.user, clean_db=clean_db, scan=scan)
                return JsonResponse({'Status': 'Processing .......'}, status=202)
            return JsonResponse({'Status': 'Query invalid .......'}, status=400)
        except ValidationError as e:
            return JsonResponse({'Status': f'Error: {e}'}, status=400)