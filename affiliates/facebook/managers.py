import json
from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db.models import Q, Sum
from django.db.models.query import QuerySet
from django.template.loader import render_to_string

import commonware.log
import requests
from caching.base import CachingManager
from tower import ugettext as _

from affiliates.base.tokens import TokenGenerator
from affiliates.base.utils import get_object_or_none


log = commonware.log.getLogger('a.facebook')


class FacebookUserManager(CachingManager):
    """
    Handles operations involving creating and retrieving users of the Facebook
    app.
    """

    def get_or_create_user_from_decoded_request(self, decoded_request):
        """
        Retrieves the user associated with the decoded request's user_id, or
        return create one if no such user exists. Returns None if the user has
        not authorized the app.
        """
        user_id = decoded_request.get('user_id', None)
        if user_id is None:
            return None, False

        return self.get_or_create(id=user_id)

    def update_user_info(self, user):
        """
        Retrieve info from the Facebook graph about the given user and update
        our cached copies of the data.
        """
        try:
            response = requests.get('https://graph.facebook.com/%s' % user.id)
        except requests.exceptions.RequestException, e:
            log.error('Error retrieving Facebook Graph information for user '
                      '%s: %s' % (user.id, e))
            return

        try:
            graph_data = json.loads(response.content)
        except ValueError:
            log.error('Malformed response from affiliates.facebook graph for user %s: %s'
                      % (user.id, response.content))
            return

        user.locale = graph_data.get('locale', False) or user.locale
        user.full_name = graph_data.get('name', False) or user.full_name
        user.first_name = graph_data.get('first_name', False) or user.first_name
        user.last_name = graph_data.get('last_name', False) or user.last_name
        user.save()

        return user

    def purge_user_data(self, user):
        """
        Purges all data associated with the given user, including the user entry
        itself.
        """
        from affiliates.facebook.models import FacebookBannerInstance

        # If this user has any banners that have been made into ads, notify the
        # admin that they're being deleted.
        ads = user.banner_instance_set.filter(
            review_status=FacebookBannerInstance.PASSED)
        if len(ads) > 0:
            subject = '[fb-affiliates-banner]User has de-authorized app'
            message = render_to_string('facebook/deauthorized_email.html', {
                'user': user,
                'ads': ads,
                'now': datetime.now()
            })
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                      [settings.FACEBOOK_CLICK_GOAL_EMAIL])

        # Delete all associated images.
        for instance in user.banner_instance_set.all():
            if instance.custom_image:
                instance.custom_image.delete()

        # Delete the user. Django will go through and delete all DB entries with
        # foreign keys pointing to this user.
        user.delete()


class FacebookAccountLinkManager(CachingManager):
    def get_token_generator(self, link):
        return TokenGenerator(link.generate_token_state,
                              delay=settings.FACEBOOK_LINK_DELAY)

    def create_link(self, facebook_user, affiliates_email):
        """
        Attempts to link a FacebookUser to an Affiliates user account. The link
        has to be confirmed via a link in the verification email before it is
        finalized.
        """
        affiliates_user = get_object_or_none(User, email=affiliates_email)
        if affiliates_user is None:
            return False

        # Exit early if the affiliates user already has an active account link.
        if affiliates_user.account_links.filter(is_active=True).exists():
            return False

        try:
            link = self.get(facebook_user=facebook_user)
        except self.model.DoesNotExist:
            link = self.model(facebook_user=facebook_user)
        link.affiliates_user = affiliates_user

        # Exit early if an active link already exists.
        if link.is_active:
            return False

        # Even if the link is old, generate a fresh activation code.
        token_generator = self.get_token_generator(link)
        link.activation_code = token_generator.generate_token()
        link.save()
        return link

    def send_activation_email(self, request, link):
        """
        Send an email to an Affiliates user to confirm that they consent to
        linking their account with a Facebook account.
        """
        subject = _('Link your Firefox Affiliates account')
        message = render_to_string('facebook/link_activation_email.html', {'link': link})
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                  [link.affiliates_user.email])

    def activate_link(self, activation_code):
        """Verify activation code and activate the corresponding link."""
        link = get_object_or_none(self.model, activation_code=activation_code)
        if link is None or link.is_active:
            return None

        token_generator = self.get_token_generator(link)
        if not token_generator.verify_token(activation_code):
            return None

        link.is_active = True
        link.save()
        return link


class FacebookClickStatsManager(CachingManager):
    def total_for_month(self, user, year, month):
        """
        Get the total amount of clicks a user achieved in a specific month.
        """
        click_stats = self.filter(banner_instance__user=user,
                                  hour__month=month, hour__year=year)
        clicks = click_stats.aggregate(Sum('clicks'))['clicks__sum']
        return clicks or 0  # `or 0` in case of None

    def total_for_user(self, user):
        """Get the total amount of clicks a user has achieved."""
        click_stats = self.filter(banner_instance__user=user)
        clicks = click_stats.aggregate(Sum('clicks'))['clicks__sum']
        return clicks or 0


class FacebookBannerManager(CachingManager):
    def get_query_set(self):
        return FacebookBannerQuerySet(self.model)

    def filter_by_locale(self, locale):
        return self.get_query_set().filter_by_locale(locale)


class FacebookBannerQuerySet(QuerySet):
    def filter_by_locale(self, locale):
        lang = locale.split('-')[0]
        locale_filter = (Q(locale_set__locale__contains=locale) |
                         Q(locale_set__locale__contains=lang))
        return self.distinct().filter(locale_filter)
