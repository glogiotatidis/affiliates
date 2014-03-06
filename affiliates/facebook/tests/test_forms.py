from django.test.client import RequestFactory

from nose.tools import eq_, ok_

from affiliates.facebook.forms import (BannerInstanceDeleteForm, FacebookAccountLinkForm,
                            FacebookBannerInstanceForm, LeaderboardFilterForm)
from affiliates.facebook.tests import (FacebookBannerFactory,
                            FacebookBannerInstanceFactory,
                            FacebookBannerLocaleFactory, FacebookUserFactory)
from affiliates.base.tests import TestCase
from affiliates.users.tests import UserFactory


class FacebookBannerInstanceFormTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def form(self, locale, form_data=None, user=None):
        request = self.factory.get('/')
        if locale is not None:
            request.locale = locale
        if user is not None:
            request.user = user
        return FacebookBannerInstanceForm(request, form_data)

    def test_no_locale(self):
        """
        If the request has no set locale, the form should accept any banner in
        any locale.
        """
        fr_banner = FacebookBannerFactory.create()
        FacebookBannerLocaleFactory.create(banner=fr_banner, locale='fr')
        en_banner = FacebookBannerFactory.create()
        FacebookBannerLocaleFactory.create(banner=en_banner, locale='en-us')

        form = self.form(None, {'text': 'asdf', 'banner': fr_banner.id})
        ok_(form.is_valid())

        form = self.form(None, {'text': 'asdf', 'banner': en_banner.id})
        ok_(form.is_valid())

    def test_with_locale(self):
        """
        If the request has a set locale, the form should only accept banners
        available in that locale.
        """
        fr_banner = FacebookBannerFactory.create()
        FacebookBannerLocaleFactory.create(banner=fr_banner, locale='fr')
        en_banner = FacebookBannerFactory.create()
        FacebookBannerLocaleFactory.create(banner=en_banner, locale='en-us')

        form = self.form('fr', {'text': 'asdf', 'banner': fr_banner.id})
        ok_(form.is_valid())

        form = self.form('fr', {'text': 'asdf', 'banner': en_banner.id})
        ok_(not form.is_valid())

    def test_similar_locales(self):
        """
        Regression test for a bug where a user in the de locale would get a
        banner choice for each de locale (de, de-at, de-ch, de-de), causing
        duplicate choices and form processing errors.
        """
        banner = FacebookBannerFactory.create()
        FacebookBannerLocaleFactory.create(banner=banner, locale='de')
        FacebookBannerLocaleFactory.create(banner=banner, locale='de-at')
        FacebookBannerLocaleFactory.create(banner=banner, locale='de-ch')
        FacebookBannerLocaleFactory.create(banner=banner, locale='de-de')

        form = self.form('de')
        eq_(len(form.fields['banner'].choices), 1)

    def test_fallback_locales(self):
        """
        If a banner is available across a language and the user is using a
        specific subset of that locale (e.g. banner is in de and user is using
        de-at), the user should be presented with that banner as an option.
        """
        FacebookBannerLocaleFactory.create(locale='de')
        form = self.form('de-at')
        eq_(len(form.fields['banner'].choices), 1)

    def test_save_locale(self):
        """The form should save the current locale on the instance."""
        locale = FacebookBannerLocaleFactory.create(locale='es')
        with self.activate('es'):
            form = self.form('es', {'text': 'asdf', 'banner': locale.banner.id},
                             user=FacebookUserFactory.create())
            instance = form.save()
            eq_(instance.locale, 'es')


class FacebookAccountLinkFormTests(TestCase):
    def test_affiliates_email_validation(self):
        """
        The affiliates_email field is only valid if an Affiliates user exists
        with the specified email address.
        """
        form = FacebookAccountLinkForm({'affiliates_email': 'dne@example.com'})
        eq_(form.is_valid(), False)

        user = UserFactory.create()
        form = FacebookAccountLinkForm({'affiliates_email': user.email})
        eq_(form.is_valid(), True)


class LeaderboardFilterFormTests(TestCase):
    def test_get_top_users(self):
        """
        Test that get_top_users, er, gets the top users ranked by
        leaderboard_position.
        """
        user1 = FacebookUserFactory.create(leaderboard_position=1)
        user2 = FacebookUserFactory.create(leaderboard_position=2)
        user3 = FacebookUserFactory.create(leaderboard_position=3)

        form = LeaderboardFilterForm()
        eq_([user1, user2, user3], list(form.get_top_users()))

    def test_exclude_unranked_users(self):
        """
        If a user has a leaderboard position of -1, do not include them in the
        top users list.
        """
        user1 = FacebookUserFactory.create(leaderboard_position=1)
        FacebookUserFactory.create(leaderboard_position=-1)
        user3 = FacebookUserFactory.create(leaderboard_position=2)

        form = LeaderboardFilterForm()
        eq_([user1, user3], list(form.get_top_users()))

    def test_filter_country(self):
        """
        If the country field is set, only return users within that country.
        """
        user1 = FacebookUserFactory.create(leaderboard_position=1, country='us')
        FacebookUserFactory.create(leaderboard_position=2, country='fr')
        user3 = FacebookUserFactory.create(leaderboard_position=3, country='us')

        form = LeaderboardFilterForm({'country': 'us'})
        eq_([user1, user3], list(form.get_top_users()))


class BannerInstanceDeleteFormTests(TestCase):
    def test_validate_user_owns_banner(self):
        """
        The delete form must validate that the user passed in the constructor
        owns the banner instance.
        """
        user = FacebookUserFactory.create()
        instance1 = FacebookBannerInstanceFactory.create(user=user)
        instance2 = FacebookBannerInstanceFactory.create()

        form = BannerInstanceDeleteForm(user, {'banner_instance': instance1.id})
        ok_(form.is_valid())

        form = BannerInstanceDeleteForm(user, {'banner_instance': instance2.id})
        ok_(not form.is_valid())
