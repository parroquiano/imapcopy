# -*- coding: utf-8 -*-
"""
    imapcopy

    Simple tool to copy folders from one IMAP server to another server.


    :copyright: (c) 2013 by Christoph Heer.
    :license: BSD, see LICENSE for more details.
"""

import sys
import hashlib
import imaplib
import logging
import argparse


class IMAP_Copy(object):
    source = {
        'host': 'localhost',
        'port': 993
    }
    source_auth = ()
    destination = {
        'host': 'localhost',
        'port': 993
    }
    destination_auth = ()
    mailbox_mapping = []

    def __init__(self, source_server, destination_server, mailbox_mapping,
                 source_auth=(), destination_auth=(), create_mailboxes=False,
                 recurse=False, skip=0, limit=0):

        self.logger = logging.getLogger("IMAP_Copy")

        self.source.update(source_server)
        self.destination.update(destination_server)
        self.source_auth = source_auth
        self.destination_auth = destination_auth

        self.mailbox_mapping = mailbox_mapping
        self.create_mailboxes = create_mailboxes

        self.skip = skip
        self.limit = limit

        self.recurse = recurse

    def _connect(self, target):
        data = getattr(self, target)
        auth = getattr(self, target + "_auth")

        self.logger.info("Connect to %s (%s)" % (target, data['host']))
        if data['port'] == 993:
            connection = imaplib.IMAP4_SSL(data['host'], data['port'])
        else:
            connection = imaplib.IMAP4(data['host'], data['port'])

        if len(auth) > 0:
            self.logger.info("Authenticate at %s" % target)
            connection.login(*auth)

        setattr(self, '_conn_%s' % target, connection)
        self.logger.info("%s connection established" % target)
        # Detecting delimiter on destination server
        code, mailbox_list = connection.list()

        mailbox_name_list = []
        for box in mailbox_list:
            parts = box.decode('utf-8').split('"')
            if len(parts) == 5:
                mailbox_name_list.append(parts[3].strip())
            elif len(parts) == 3:
                mailbox_name_list.append(parts[2].strip())

        mailbox_names = ', '.join(mailbox_name_list)
        self.logger.info("%s has the following mailboxes: %s" % (target, mailbox_names))

        self.delimiter = mailbox_list[0].split(b'"')[1]

    def connect(self):
        self._connect('source')
        self._connect('destination')

    def _disconnect(self, target):
        if not hasattr(self, '_conn_%s' % target):
            return

        connection = getattr(self, '_conn_%s' % target)
        if connection.state == 'SELECTED':
            connection.close()
            self.logger.info("Close mailbox on %s" % target)

        self.logger.info("Disconnect from %s server" % target)
        connection.logout()
        delattr(self, '_conn_%s' % target)

    def disconnect(self):
        self._disconnect('source')
        self._disconnect('destination')

    def copy(self, source_mailbox, destination_mailbox, skip, limit, recurse=True):

        # There should be no files stored in / so we are bailing out
        if source_mailbox == '':
            return

        # Connect to source and open mailbox
        status, data = self._conn_source.select(source_mailbox, True)
        if status != "OK":
            self.logger.error("Couldn't open source mailbox %s" %
                              source_mailbox)
            sys.exit(2)

        # Connect to destination and open or create mailbox
        status, data = self._conn_destination.select(destination_mailbox)
        if status != "OK" and not self.create_mailboxes:
            self.logger.error("Couldn't open destination mailbox %s" %
                              destination_mailbox)
            sys.exit(2)
        else:
            self.logger.info("Create destination mailbox %s" %
                             destination_mailbox)
            self._conn_destination.create(destination_mailbox)
            status, data = self._conn_destination.select(destination_mailbox)

        # Look for mails
        self.logger.info("Looking for mails in %s" % source_mailbox)
        status, data = self._conn_source.search(None, 'ALL')
        data = data[0].split()
        mail_count = len(data)

        self.logger.info("Start copy %s => %s (%d mails)" % (
            source_mailbox, destination_mailbox, mail_count))

        progress_count = 0
        copy_count = 0

        for msg_num in data:
            progress_count += 1
            if progress_count <= skip:
                self.logger.info("Skipping mail %d of %d" % (
                    progress_count, mail_count))
                continue
            else:
                status, data = self._conn_source.fetch(msg_num, '(RFC822 FLAGS INTERNALDATE)')

                flag_line = data[0][0].decode('ascii')
                if flag_line.find('FLAGS') < 0 and len(data) > 1:
                    flag_line = data[1].decode('ascii')
                message = data[0][1]

                flags = flag_line[flag_line.index('FLAGS (') + len('FLAGS (') - 1:flag_line.index(' INTERNALDATE')]
                end_date_str = flag_line.find(' RFC822')
                if end_date_str < 0:
                    end_date_str = flag_line.index(')', flag_line.index('INTERNALDATE '))
                internaldate = flag_line[flag_line.index('INTERNALDATE ') + len('INTERNALDATE '):end_date_str]

                self._conn_destination.append(
                    destination_mailbox, flags, internaldate, message,
                )

                copy_count += 1
                message_sha1 = hashlib.sha1(message).hexdigest()

                self.logger.info("Copy mail %d of %d (copy_count=%d, sha1(message)=%s)" % (
                    progress_count, mail_count, copy_count, message_sha1))

                if limit > 0 and copy_count >= limit:
                    self.logger.info("Copy limit %d reached (copy_count=%d)" % (
                        limit, copy_count))
                    break

        self.logger.info("Copy complete %s => %s (%d out of %d mails copied)" % (
            source_mailbox, destination_mailbox, copy_count, mail_count))

        if self.recurse and recurse:
            self.logger.info("Getting list of mailboxes under %s" % source_mailbox)
            connection = self._conn_source
            typ, data = connection.list(source_mailbox)
            for d in data:
                if d:
                    l_resp = d.split('"')
                    # response = '(\HasChildren) "/" INBOX'
                    if len(l_resp) == 3:

                        source_mbox = d.split('"')[2].strip()
                        # make sure we don't have a recursive loop
                        if source_mbox != source_mailbox:
                            # maybe better use regex to replace only start of the souce name
                            dest_mbox = source_mbox.replace(source_mailbox, destination_mailbox)
                            self.logger.info("starting copy of mailbox %s to %s " % (source_mbox, dest_mbox))
                            self.copy(source_mbox, dest_mbox, skip, limit, False)

    def run(self):
        try:
            self.connect()
            for source_mailbox, destination_mailbox in self.mailbox_mapping:

                if ' ' in source_mailbox and '"' not in source_mailbox:
                    source_mailbox = '"%s"' % source_mailbox
                if ' ' in destination_mailbox and '"' not in destination_mailbox:
                    destination_mailbox = '"%s"' % destination_mailbox

                self.copy(source_mailbox, destination_mailbox, self.skip, self.limit)
        finally:
            self.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source',
                        help="Source host ex. imap.googlemail.com:993")
    parser.add_argument('source_auth', metavar='source-auth',
                        help="Source host authentication ex. "
                             "username@host.de:password")

    parser.add_argument('destination',
                        help="Destination host ex. imap.otherhoster.com:993")
    parser.add_argument('destination_auth', metavar='destination-auth',
                        help="Destination host authentication ex. "
                             "username@host.de:password")

    parser.add_argument('mailboxes', type=str, nargs='+',
                        help='List of mailboxes alternate between source '
                             'mailbox and destination mailbox.')
    parser.add_argument('-c', '--create-mailboxes', dest='create_mailboxes',
                        action="store_true", default=False,
                        help='Create the mailboxes on destination')
    parser.add_argument('-r', '--recurse', dest='recurse', action="store_true",
                        default=False, help='Recurse into submailboxes')
    parser.add_argument('-q', '--quiet', action="store_true", default=False,
                        help='ppsssh... be quiet. (no output)')
    parser.add_argument('-v', '--verbose', action="store_true", default=False,
                        help='more output please (debug level)')

    def check_negative(value):
        ivalue = int(value)
        if ivalue < 0:
            raise argparse.ArgumentTypeError("%s is an invalid positive integer value" % value)
        return ivalue

    parser.add_argument("-s", "--skip", default=0, metavar="N", type=check_negative,
                        help='skip the first N message(s)')
    parser.add_argument("-l", "--limit", default=0, metavar="N", type=check_negative,
                        help='only copy N number of message(s)')

    args = parser.parse_args()

    _source = args.source.split(':')
    source = {'host': _source[0]}
    if len(_source) > 1:
        source['port'] = int(_source[1])

    _destination = args.destination.split(':')
    destination = {'host': _destination[0]}
    if len(_destination) > 1:
        destination['port'] = int(_destination[1])

    source_auth = tuple(args.source_auth.split(':'))
    destination_auth = tuple(args.destination_auth.split(':'))

    if len(args.mailboxes) % 2 != 0:
        print("Not valid count of mailboxes!")
        sys.exit(1)

    mailbox_mapping = list(zip(args.mailboxes[::2], args.mailboxes[1::2]))

    imap_copy = IMAP_Copy(source, destination, mailbox_mapping, source_auth,
                          destination_auth, create_mailboxes=args.create_mailboxes,
                          recurse=args.recurse, skip=args.skip, limit=args.limit)

    streamHandler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    streamHandler.setFormatter(formatter)
    imap_copy.logger.addHandler(streamHandler)

    if not args.quiet:
        streamHandler.setLevel(logging.INFO)
        imap_copy.logger.setLevel(logging.INFO)
    if args.verbose:
        streamHandler.setLevel(logging.DEBUG)
        imap_copy.logger.setLevel(logging.DEBUG)

    try:
        imap_copy.run()
    except KeyboardInterrupt:
        imap_copy.disconnect()


if __name__ == '__main__':
    main()
