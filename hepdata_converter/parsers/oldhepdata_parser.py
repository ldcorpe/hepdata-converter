from string import lower
from hepdata_converter.common import OptionInitMixin, Option
from hepdata_converter.parsers import Parser, ParsedData, BadFormat, Table
import StringIO
import copy
import re


class HEPTable(Table):
    """Extension of Table including some place for temporary data needed during conversion
    """
    def __init__(self, index=None, data_file=None, table_name=None):
        super(HEPTable, self).__init__(index, data_file, table_name)

        self.xheaders = []
        self.yheaders = []


class OldHEPData(Parser):
    """Parser for Old HEPData format
    """
    help = 'Parses OLDHepData format - example OLD HepData input format: http://hepdata.cedar.ac.uk/resource/sample.input'

    options = {
        'strict': Option('strict', required=True, default=True,
                         help='if specified any additional keywords in OldHEPData file will raise an error'),
        'use_additional_data': Option('use_additional_data', required=True, default=False,
                                      help=('if specified additional data which does not have equivalent in new HEPData format'
                                            ' will be appended to comment section of the output document'))
    }

    def reset(self):
        """Clean any processing data, and prepare object for reuse
        """
        self.current_table = None
        self.tables = []
        self.data = [{}]
        self.additional_data = {}
        self.lines = []
        self.set_state('document')
        self.current_file = None

    def set_state(self, state):
        if state not in OldHEPData.states:
            raise ValueError("unknown state")
        self.current_state = state

    def __init__(self, **kwargs):
        """Constructor
        :param use_additional_data: if set to True additional data which does not have equivalent in new HEPData format
        will be appended to comment section of the output document
        :type use_additional_data: bool

        :param strict: if set to True, any additional keywords (not specified by documentation)
        will raise BadFormat exception during parsing
        :type strict: bool
        """


        # mapping of OLDHEP format's keywords to proper parsing functions
        # all possible keywords are specified here, the ones which aren't used
        # by new data format are either mapped to _pass method which does nothing,
        # or bound to _add_to_comment which adds the specified data to comment section of the
        # output

        # functions are specified by states in which parser may be, it may either be parsing table or document
        # for those two states different sets of directives are permitted
        self.mapping = {
            'document': {
                'reference':  self._parse_reference,
                'dataset':    self._set_table,

                # additional data which have no one to one mapping to new YAML format
                'author':     self._bind_parse_addtional_data('author'),
                'doi':        self._bind_parse_addtional_data('doi'),
                'status':     self._bind_parse_addtional_data('status'),
                'experiment': self._bind_parse_addtional_data('experiment'),
                'detector':   self._bind_parse_addtional_data('detector'),
                'title':      self._bind_parse_addtional_data('title'),

                # add it to record_ids
                'spiresId':   self._bind_parse_record_ids('spires'),
                'inspireId':  self._bind_parse_record_ids('inspire'),
                'cdsId':      self._bind_parse_record_ids('cds'),
                'durhamId':   self._bind_parse_record_ids('durham'),

                'comment':    self._parse_document_comment,
                'E':          self._pass
            },
            'table': {
                'dataend':    self._set_document,
                'location':   self._bind_set_table_metadata('location'),
                'dscomment':  self._bind_set_table_metadata('description', True),
                'reackey':    self._parse_reackey,
                'qual':       self._parse_qual,
                'data':       self._parse_table_data,
                'xheader':    self._parse_xheaders,
                'yheader':    self._parse_yheaders,
                'obskey':     self._parse_obskey,
                'E':          self._pass
            }
        }

        OptionInitMixin.__init__(self, options=kwargs)

    def parse(self, data_in):
        # clean any possible data from previous parsing
        self.reset()

        # for convenience, if provided data_in is string instead of
        # filelike object wrap in into StringIO.StringIO
        if isinstance(data_in, str) or isinstance(data_in, unicode):
            data_in = StringIO.StringIO(data_in)

        self.current_file = data_in

        # parse the file
        while self._parse_line(self.current_file):
            pass

        if self.use_additional_data:
            if len(self.additional_data) > 0:
                self.data[0]['comment'] += 'ADDITIONAL DATA IMPORTED FROM OLD HEPDATA FORMAT: \n'
            for key in self.additional_data:
                for element in self.additional_data[key]:
                    self.data[0]['comment'] += "%s: %s" % (key, element)

        return ParsedData(self.data, self.tables)

    def _parse_line(self, file):
        """Parse single line (or more if particular keyword actually demands it)

        :param file:
        :type file: file
        """

        line = self._strip_comments(file.readline())
        # check if the file ended
        if not line:
            return False

        # line was empty or it was a comment, continue
        if line == '\n':
            return True

        # retrieve keyword and its value
        reg = re.search("^\*(?P<key>[^:#]*)(:(?P<value>.*))?$", line)
        if reg:
            key = reg.group('key').strip()
            value = reg.group('value')

            if key in self.mapping[self.current_state]:
                self.mapping[self.current_state][key](value)
            elif self.strict:
                raise BadFormat("unknown key: *%s" % key)
        else:
            raise BadFormat("line can not be parsed: %s" % line)

        return True

    def _parse_reference(self, data):
        """

        :param data:
        :type data: str
        """
        if 'additional_resources' not in self.data[0]:
            self.data[0]['additional_resources'] = []

        self.data[0]['additional_resources'].append({
            'location': data.split(' : ')[0],
            'description': ''
        })

    def _set_table(self, data):
        """Set current parsing state to 'table',
        create new table object and add it to tables collection
        """
        self.set_state('table')
        self.current_table = HEPTable(index=len(self.tables))
        self.tables.append(self.current_table)
        self.data.append(self.current_table.metadata)

    def _set_document(self, data):
        """Set current parsing state to 'document',
        set current_table to None
        """
        self.set_state('document')
        self.current_table = None

    def _pass(self, data):
        """Empty processing function, map it to keywords if they're not used in the new YAML format
        """
        pass

    def _parse_table_data(self, data):
        """Parse dataset data of the original HEPData format

        :param data: header of the table to be parsed
        :raise ValueError:
        """
        header = data.split(':')

        self.current_table.data_header = header

        for i in range(len(header)):
            header[i] = header[i].strip()

        x_count = header.count('x')
        y_count = header.count('y')

        # use deepcopy to avoid references in yaml... may not be required, and will very probably be refactored
        # TODO - is this aproperiate behavior, or are references in YAML files acceptable, they are certainly less human readable
        self.current_table.data = {'independent_variables': [{'header': self.current_table.xheaders[i] if i < len(self.current_table.xheaders) else copy.deepcopy(self.current_table.xheaders[-1]),
                                                              'values': []} for i in range(x_count)],
                                      'dependent_variables': [{'header': self.current_table.yheaders[i] if i < len(self.current_table.yheaders) else copy.deepcopy(self.current_table.yheaders[-1]),
                                                               'qualifiers': [self.current_table.qualifiers[j][i] for j in range(len(self.current_table.qualifiers)) ],
                                                               'values': []} for i in range(y_count)]}

        xy_mapping = []

        current_x_count = 0
        current_y_count = 0

        for i in range(len(header)):
            if header[i] == 'x':
                xy_mapping.append(current_x_count)
                current_x_count += 1
            if header[i] == 'y':
                xy_mapping.append(current_y_count)
                current_y_count += 1

        last_index = self.current_file.pos
        line = self._strip_comments(self.current_file.readline())

        while line and not line.startswith('*'):
            data_entry_elements = line.split(';')[:-1] # split and also strip newline character at the end

            if len(data_entry_elements) == len(header):
            # this is kind of a big stretch... I assume that x is always first
                for i in range(len(header)):
                    single_element = data_entry_elements[i]

                    if header[i] == 'x': # independent variables
                        r = re.search('(?P<low>[0-9]+\.?[0-9]*)( +TO +(?P<height>[0-9]+\.?[0-9]*))?', single_element)
                        if r:
                            if r.group('height') is not None:
                                single_element = {'low': r.group('low'), 'height': r.group('height')}
                            else:
                                single_element = r.group('low')
                            self.current_table.data['independent_variables'][xy_mapping[i]]['values'].append(single_element)

                    elif header[i] == 'y': # independant variable
                        r = re.search('( *)(?P<value>[0-9]+\.?[0-9]*)( *)\+(?P<err_p>[0-9]+\.?[0-9]*),(?P<err_m>\-[0-9]+\.?[0-9]*)( *)\((?P<err_sys>[^()]*)\)', single_element)
                        element = {'errors': []}
                        if r:
                            element['value'] = r.group('value')
                            element['errors'] += [{'label': 'stat', 'asymerror': {'plus': r.group('err_p'), 'minus': r.group('err_m')}}]

                        else:
                            r = re.search('( *)(?P<value>[0-9]+\.?[0-9]*)( *)\+-( *)(?P<error>[0-9]+\.?[0-9]*)( *)\((?P<err_sys>[^()]*)\)', single_element)
                            if r:
                                element['value'] = r.group('value')
                                element['errors'] += [{'label': 'stat', 'symerror': r.group('error')}]

                        err_sys = r.group('err_sys').split('DSYS=')

                        for err in err_sys:
                            if not err:
                                continue
                            error = {}
                            label = 'sys'
                            r = re.search('^(?P<error>[0-9]+\.?[0-9]*)(\:(?P<label>.*))?,?$', err)
                            if r:
                                if r.group('label'):
                                    label += ','+r.group('label')
                                error = {'symerror': r.group('error') }

                            else:
                                #TODO - check some assumptions - will '+' always be first? and '-' always second?
                                r = re.search('\+(?P<err_p>[0-9]+\.?[0-9]*)\,(?P<err_m>\-[0-9]+\.?[0-9]*)(\:(?P<label>.*))?', err)
                                if r:
                                    if r.group('label'):
                                        label += ','+r.group('label')
                                    error = {'asymerror': {'plus': r.group('err_p'), 'minus': r.group('err_m')}}
                            if not r:
                                # error happened
                                raise ValueError("Error while parsing data")

                            error['label'] = label
                            element['errors'].append(error)
                        self.current_table.data['dependent_variables'][xy_mapping[i]]['values'].append(element)

            last_index = self.current_file.pos
            l = self.current_file.readline()
            line = self._strip_comments(l)

        self.current_file.seek(last_index)

    def _parse_reackey(self, data):
        """Parse reackey attribute of the old HEPData format

        example:
        *reackey: P P --> Z0 Z0 X

        :param data: data to be parsed
        :type data: str
        """
        self.current_table.reactions.append(data)

    def _parse_obskey(self, data):
        """Parse obskey attribute of the old HEPData format

        example:
        *obskey: DSIG/DPT

        :param data: data to be parsed
        :type data: str
        """
        self.current_table.observables.append(data)

    def _parse_energies(self, data):
        """Add energy given in data to tables energies
        this method is here for compleatness sake, it's used in only one other place so
        can be safely extracted

        :param data: data to be appended to table's energies
        :type data: str
        """
        self.current_table.energies.append(data)

    def _parse_qual(self, data):
        """Parse qual attribute of the old HEPData format

        example qual:
        *qual: RE : P P --> Z0 Z0 X

        :param data: data to be parsed
        :type data: str
        """
        list = []
        headers = data.split(':')
        name = headers[0].strip()

        name = name.split(' IN ')
        units = None
        if len(name) > 1:
            units = name[1].strip()
        name = name[0].strip()

        for header in headers[1:]:
            xheader = {'name': name}
            if units:
                xheader['units'] = units

            xheader['value'] = header.strip()
            list.append(xheader)

            #:TODO: VERY RISKY, check energies and so on
            #this is a hack, I hope there is better way to do this:
            if name == 'SQRT(S)' and lower(units) in ('gev', 'tev', 'mev'):
                if xheader['value'] not in self.current_table.energies:
                    self._parse_energies(xheader['value'])

        self.current_table.qualifiers.append(list)

    def _parse_header(self, data):
        """Parse header (xheader or yheader)

        :param data: data to be parsed
        :type data: str
        :return: list with header's data
        :rtype: list
        """
        return_list = []

        headers = data.split(':')

        for header in headers:
            header = header.split(' IN ')
            xheader = {'name': header[0].strip()}
            if len(header) > 1:
                xheader['units'] = header[1].strip()
            return_list.append(xheader)

        return return_list

    def _parse_xheaders(self, data):
        """parse xheaders from old HEPData format

        :param data: data with xheaders to be parsed
        :type data: str
        """
        self.current_table.xheaders += self._parse_header(data)

    def _parse_yheaders(self, data):
        """parse yheaders from old HEPData format

        :param data: data with yheaders to be parsed
        :type data: str
        """
        self.current_table.yheaders += self._parse_header(data)

    @staticmethod
    def _strip_comments(line):
        """Processes line stripping any comments from it

        :param line: line to be processed
        :type line: str
        :return: line with removed comments
        :rtype: str
        """
        if line == '':
            return line
        r = re.search('(?P<line>[^#]*)(#(?P<comment>.*))?', line)
        if r:
            line = r.group('line')
            if not line.endswith('\n'):
                line += '\n'
            return line
        return '\n'

    def _read_multiline(self, init_data):
        """Reads multiline symbols (ususally comments)

        :param init_data: initial data (parsed from the line containing keyword)
        :return: parsed value of the multiline symbol
        :rtype: str
        """
        result = init_data

        while True:
            last_index = self.current_file.pos
            line_raw = self.current_file.readline()

            # don't add newlines from full line comments
            if line_raw[0] == '#':
                continue

            # now strip comments
            # TODO - is it aproperiate behavior?
            data = self._strip_comments(line_raw)
            # EOF, stop here
            if not data:
                break

            # we arrived to the next command, step back and break
            if len(data.strip()) >= 1 and data.strip()[0] == '*':
                self.current_file.seek(last_index)
                break

            result += data
        return result

    def _parse_document_comment(self, data):
        self.data[0]['comment'] = self._read_multiline(data)

    def _bind_set_table_metadata(self, key,  multiline=False):
        """Returns parsing function which will parse data as text, and add it to the table metatadata dictionary
        with the provided key

        :param key: dictionary key under which parsed data will be added to table.metadata
        :type key: str
        :param multiline: if True this attribute will be treated as multiline
        :type multiline: bool
        :return: function with bound key and multiline attributes
        :rtype: Function
        """
        def set_table_metadata(self, data):
            if multiline:
                data = self._read_multiline(data)
            self.current_table.metadata[key] = data

        # method must be bound, so we use __get__
        return set_table_metadata.__get__(self)

    def _bind_parse_record_ids(self, key):
        def _parse_record_ids(self, data):
            if 'record_ids' not in self.data[0]:
                self.data[0]['record_ids'] = []

            record_id = {key, data}

            if self.data[0]['record_ids'].count(record_id) == 0:
                self.data[0]['record_ids'].append(record_id)
            elif self.strict:
                raise BadFormat("duplicated record: *%s" % key)

        # method must be bound, so we use __get__
        return _parse_record_ids.__get__(self)

    def _bind_parse_addtional_data(self, key, multiline=False):
        """Returns parsing function which will parse data as text, and add it to the table additional data dictionary
        with the provided key

        :param key: dictionary key under which parsed data will be added to table.metadata
        :type key: str
        :param multiline: if True this attribute will be treated as multiline
        :type multiline: bool
        :return: function with bound key and multiline attributes
        :rtype: Function
        """

        def _set_additional_data_bound(self, data):
            """Concrete method for setting additional data
            :param self:
            :type self: OldHEPData
            """
            # if it's multiline, parse it
            if multiline:
                data = self._read_multiline(data)

            if key not in self.additional_data:
                self.additional_data[key] = []
            self.additional_data[key].append(data)

        # method must be bound, so we use __get__
        return _set_additional_data_bound.__get__(self)

    # employing full fledged FSM for only two states is kind of an overkill, so
    # I'll just define them here...
    states = ['document', 'table']