from os import urandom
from time import time

from cryptography.exceptions import InvalidKey
from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Unauthorized

from cc_agency.commons.helper import generate_secret, create_kdf


AUTHORIZATION_COOKIE_KEY = 'authorization_cookie'
DEFAULT_REALM = 'Please fill in username and password'


class Auth:
    class User:
        """
        Defines a authenticated user
        """
        def __init__(self, username, is_admin):
            """
            Creates a authenticated user

            :param username: The username of the user
            :param is_admin: Whether the user is an admin
            """
            self.username = username
            self.authentication_cookie = None
            self.verified_by_credentials = False
            self.is_admin = is_admin

    def __init__(self, conf, mongo):
        self._num_login_attempts = conf.d['broker']['auth']['num_login_attempts']
        self._block_for_seconds = conf.d['broker']['auth']['block_for_seconds']
        self.tokens_valid_for_seconds = conf.d['broker']['auth']['tokens_valid_for_seconds']

        self._mongo = mongo

    def create_user(self, username, password, is_admin):
        salt = urandom(16)
        kdf = create_kdf(salt)
        user = {
            'username': username,
            'password': kdf.derive(password.encode('utf-8')),
            'salt': salt,
            'is_admin': is_admin
        }
        self._mongo.db['users'].update_one({'username': username}, {'$set': user}, upsert=True)

    @staticmethod
    def _create_unauthorized(description, realm=DEFAULT_REALM):
        www_authenticate = WWWAuthenticate()
        www_authenticate.set_basic(realm=realm)
        return Unauthorized(description=description, www_authenticate=www_authenticate.to_header())

    def verify_user(self, auth, cookies, ip):
        """
        Checks if a http request with the given auth is authorized. If it is authorized a AuthUser object is returned,
        otherwise an Unauthorized Exception is raised, containing the www_authenticate header.

        :param auth: The authorization header of a http request
        :type auth: werkzeug.datastructures.Authorization
        :param cookies: The cookies of the request, to check against the authorization cookie
        :param ip: The ip address of the request
        :type ip: str
        :return: If authentication was successful: An Auth.User object containing the username and other information
                                                   about the authorized user
                 If authentication failed: None
        :rtype: Auth.User

        :raise Unauthorized: Raises an Unauthorized exception, if authorization failed.
        """
        if not auth:
            raise Auth._create_unauthorized(description='Missing Authentication information')

        username = auth.username
        request_password = auth.password

        db_user = self._mongo.db['users'].find_one({'username': username})  # type: dict

        if not db_user:
            raise Auth._create_unauthorized(description='Could not find user "{}".'.format(username))

        user = Auth.User(db_user['username'], db_user['is_admin'])

        salt = db_user['salt']
        del db_user['salt']

        if self._is_blocked_temporarily(user.username):
            raise Auth._create_unauthorized(
                description='The user "{}" is currently blocked due to frequent invalid login attempts.'.format(username)
            )

        if self._verify_user_by_cookie(user, cookies, ip):
            user.authentication_cookie = cookies.get(AUTHORIZATION_COOKIE_KEY)
            return user

        if self._verify_user_by_credentials(db_user['password'], request_password, salt):
            user.verified_by_credentials = True
            # create authorization cookie
            user.authentication_cookie = self._issue_token(user, ip)
            return user

        self._add_block_entry(username)
        raise Auth._create_unauthorized('Invalid username/password combination for user "{}".'.format(username))

    def _is_blocked_temporarily(self, username):
        """
        Returns whether the given username is blocked at the moment, because of an invalid login attempt.

        :param username: The username to check against
        :type username: str
        :return: True, if the username is blocked, otherwise False
        :rtype: bool
        """
        self._mongo.db['block_entries'].delete_many({'timestamp': {'$lt': time() - self._block_for_seconds}})
        block_entries = list(self._mongo.db['block_entries'].find({'username': username}))

        if len(block_entries) > self._num_login_attempts:
            return True

        return False

    def _add_block_entry(self, username):
        self._mongo.db['block_entries'].insert_one({
            'username': username,
            'timestamp': time()
        })
        print('Unverified login attempt: added block entry!')

    def _issue_token(self, user, ip):
        """
        Creates a token in the mongo token db with the fields: [username, ip, salt, token, timestamp] and returns it.

        :param user: The user for which a token should be created
        :type user: Auth.User
        :param ip: The ip address of the user request
        :type ip: str
        :return: The created token
        :rtype: bytes
        """
        salt = urandom(16)
        kdf = create_kdf(salt)
        token = generate_secret()
        self._mongo.db['tokens'].insert_one({
            'username': user.username,
            'ip': ip,
            'salt': salt,
            'token': kdf.derive(token.encode('utf-8')),
            'timestamp': time()
        })
        return token

    def _verify_user_by_cookie(self, user, cookies, ip):
        """
        Returns whether the given user is authorized by the token, given by the received cookies.

        :param user: The user to check for
        :type user: Auth.User
        :param cookies: The cookies given by the user request
        :type cookies: dict
        :param ip: The ip address of the user request
        :type ip: str
        :return: True, if the given user could be authorized by an authorization cookie
        :rtype: bool
        """
        # delete old tokens
        self._mongo.db['tokens'].delete_many({'timestamp': {'$lt': time() - self.tokens_valid_for_seconds}})

        # get authorization cookie
        token = cookies.get(AUTHORIZATION_COOKIE_KEY)
        if token is None:
            return False

        cursor = self._mongo.db['tokens'].find(
            {'username': user.username, 'ip': ip},
            {'token': 1, 'salt': 1}
        )
        for c in cursor:
            kdf = create_kdf(c['salt'])
            try:
                kdf.verify(token.encode('utf-8'), c['token'])
                return True
            except InvalidKey:  # if token does not fit, try the next
                pass

        return False

    @staticmethod
    def _verify_user_by_credentials(db_password, request_password, salt):
        """
        Checks if the given user/password combination is authorized

        :param db_password: The user password as stored in the db
        :type db_password: str
        :param request_password: The password string of the user given by the authorization data of the user request
        :param salt: The salt value of the user from the db
        :return:
        """
        kdf = create_kdf(salt)
        try:
            kdf.verify(request_password.encode('utf-8'), db_password)
        except InvalidKey:
            return False

        return True
